from __future__ import annotations
import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.config import settings
from apps.api.app.database import get_db
from apps.api.app.models import ProjectModel
from apps.api.app.security.auth import UserContext, verify_api_key
from packages.context_engine.indexing.indexer import RepositoryIndexer
from packages.shared.logging import logger
from packages.shared.schemas import ProjectCreate, ProjectResponse, gen_id, utc_now

router = APIRouter(prefix="/api/projects", tags=["Projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_in: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(verify_api_key),
):
    project_id = gen_id("proj")
    user_id = current_user.user_id or "default"

    # Strict isolated workspace sandbox path per user and project
    base_ws = Path(settings.WORKSPACE_DIR).resolve()
    ws_dir = base_ws / user_id / project_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    repo_path = str(ws_dir)

    # If git url provided, clone into the isolated workspace
    git_url = project_in.git_url or (project_in.repo_url if project_in.repo_url and project_in.repo_url.startswith("http") else None)
    if git_url:
        try:
            import git
            await asyncio.to_thread(git.Repo.clone_from, git_url, repo_path)
        except Exception as e:
            logger.warning(f"Git clone failed for {git_url} ({e}), initializing empty repo in workspace.")
            try:
                import git
                repo = git.Repo.init(repo_path)
                readme = ws_dir / "README.md"
                readme.write_text(f"# {project_in.name}\n\nWorkspace initialized.\n", encoding="utf-8")
                repo.git.add(A=True)
                repo.index.commit("Initial commit")
            except Exception:
                pass
    else:
        # Initialize an empty git repository in the workspace
        try:
            import git
            repo = git.Repo.init(repo_path)
            readme = ws_dir / "README.md"
            readme.write_text(f"# {project_in.name}\n\nWorkspace initialized for {project_in.name}.\n", encoding="utf-8")
            repo.git.add(A=True)
            repo.index.commit("Initial commit")
        except Exception as e:
            logger.warning(f"Git repo init error: {e}")

    db_proj = ProjectModel(
        id=project_id,
        user_id=user_id,
        name=project_in.name,
        repo_url=git_url,
        repo_path=repo_path,
        default_branch=project_in.default_branch or "main",
        description=project_in.description,
        status="ready",
        created_at=utc_now(),
    )
    db.add(db_proj)
    await db.commit()
    await db.refresh(db_proj)

    # Trigger indexing in background thread
    try:
        indexer = RepositoryIndexer(repo_path)
        await asyncio.to_thread(indexer.index)
        db_proj.last_indexed_at = utc_now()
        await db.commit()
    except Exception:
        pass

    return ProjectResponse(
        id=db_proj.id,
        name=db_proj.name,
        repo_url=db_proj.repo_url,
        repo_path=db_proj.repo_path,
        default_branch=db_proj.default_branch,
        description=db_proj.description,
        status=db_proj.status,
        created_at=db_proj.created_at,
        last_indexed_at=db_proj.last_indexed_at,
    )


@router.post("/upload", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project_with_files(
    name: str = Form(...),
    git_url: Optional[str] = Form(None),
    paths: Optional[str] = Form(None),  # JSON array of relative paths matching files
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(verify_api_key),
):
    """
    Creates a project by uploading local folder files directly from the user's laptop/browser.
    Saves files into an isolated cloud sandbox workspace for the authenticated user.
    """
    project_id = gen_id("proj")
    user_id = current_user.user_id or "default"

    base_ws = Path(settings.WORKSPACE_DIR).resolve()
    ws_dir = base_ws / user_id / project_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    repo_path = str(ws_dir)

    parsed_paths: List[str] = []
    if paths:
        try:
            parsed_paths = json.loads(paths)
        except Exception:
            pass

    # Save uploaded files into the workspace directory
    for idx, uploaded_file in enumerate(files):
        rel_path = parsed_paths[idx] if idx < len(parsed_paths) else (uploaded_file.filename or f"file_{idx}")
        # Clean relative path to prevent directory traversal
        clean_rel = os.path.normpath(rel_path).lstrip("/\\.")
        target_file = ws_dir / clean_rel
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(target_file, "wb") as f:
            shutil.copyfileobj(uploaded_file.file, f)

    # Initialize git repo in the workspace
    try:
        import git
        repo = git.Repo.init(repo_path)
        repo.git.add(A=True)
        repo.index.commit("Initial project upload from local workspace")
    except Exception as e:
        logger.warning(f"Git init after file upload error: {e}")

    db_proj = ProjectModel(
        id=project_id,
        user_id=user_id,
        name=name,
        repo_url=git_url,
        repo_path=repo_path,
        default_branch="main",
        status="ready",
        created_at=utc_now(),
    )
    db.add(db_proj)
    await db.commit()
    await db.refresh(db_proj)

    # Trigger indexing
    try:
        indexer = RepositoryIndexer(repo_path)
        await asyncio.to_thread(indexer.index)
        db_proj.last_indexed_at = utc_now()
        await db.commit()
    except Exception:
        pass

    return ProjectResponse(
        id=db_proj.id,
        name=db_proj.name,
        repo_url=db_proj.repo_url,
        repo_path=db_proj.repo_path,
        default_branch=db_proj.default_branch,
        description=db_proj.description,
        status=db_proj.status,
        created_at=db_proj.created_at,
        last_indexed_at=db_proj.last_indexed_at,
    )


@router.get("", response_model=List[ProjectResponse])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(verify_api_key),
):
    query = select(ProjectModel).order_by(ProjectModel.created_at.desc())
    if not current_user.is_admin:
        # Filter projects belonging to current user or legacy shared projects
        query = query.where(
            (ProjectModel.user_id == current_user.user_id) | (ProjectModel.user_id.is_(None))
        )
    result = await db.execute(query)
    projects = result.scalars().all()
    return [
        ProjectResponse(
            id=p.id,
            name=p.name,
            repo_url=p.repo_url,
            repo_path=p.repo_path,
            default_branch=p.default_branch,
            description=p.description,
            status=p.status,
            created_at=p.created_at,
            last_indexed_at=p.last_indexed_at,
        )
        for p in projects
    ]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(verify_api_key),
):
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    proj = result.scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    if not current_user.is_admin and proj.user_id and proj.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access to this project is forbidden.")
    return ProjectResponse(
        id=proj.id,
        name=proj.name,
        repo_url=proj.repo_url,
        repo_path=proj.repo_path,
        default_branch=proj.default_branch,
        description=proj.description,
        status=proj.status,
        created_at=proj.created_at,
        last_indexed_at=proj.last_indexed_at,
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(verify_api_key),
):
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    proj = result.scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    if not current_user.is_admin and proj.user_id and proj.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access to delete this project is forbidden.")
    
    # Clean up workspace directory if inside WORKSPACE_DIR
    try:
        base_ws = Path(settings.WORKSPACE_DIR).resolve()
        if proj.repo_path and str(Path(proj.repo_path).resolve()).startswith(str(base_ws)):
            shutil.rmtree(proj.repo_path, ignore_errors=True)
    except Exception:
        pass

    await db.delete(proj)
    await db.commit()

