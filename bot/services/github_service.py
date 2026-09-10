import asyncio
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

_GITHUB_HOST = "github.com"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_API_ROOT = "https://api.github.com/repos"


@dataclass(frozen=True)
class GithubRepository:
    owner: str
    repo: str
    url: str
    full_name: str
    description: str | None


@dataclass(frozen=True)
class RepositoryValidation:
    status: str
    repository: GithubRepository | None = None


@dataclass(frozen=True)
class ArchiveDownload:
    status: str
    path: Path | None = None


class GithubService:
    """GitHub API access using only normalized owner/repository identifiers."""

    def __init__(self, max_archive_size: int) -> None:
        if max_archive_size <= 0:
            raise ValueError("max_archive_size must be positive")
        self.max_archive_size = max_archive_size

    @staticmethod
    def parse_repository_url(raw_url: str) -> GithubRepository | None:
        try:
            parsed = urlsplit(raw_url.strip())
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or parsed.hostname != _GITHUB_HOST:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            return None
        owner, repo = parts
        repo = repo.removesuffix(".git")
        if (
            not owner
            or not repo
            or not _NAME_RE.fullmatch(owner)
            or not _NAME_RE.fullmatch(repo)
        ):
            return None
        return GithubRepository(
            owner=owner,
            repo=repo,
            url=f"https://github.com/{owner}/{repo}",
            full_name=f"{owner}/{repo}",
            description=None,
        )

    async def validate_repository(self, raw_url: str) -> RepositoryValidation:
        repository = self.parse_repository_url(raw_url)
        if not repository:
            return RepositoryValidation("invalid_url")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                response = await client.get(
                    f"{_API_ROOT}/{repository.owner}/{repository.repo}",
                    headers={"Accept": "application/vnd.github+json"},
                )
        except httpx.HTTPError as exc:
            log.warning("GitHub repository validation failed: %s", exc)
            return RepositoryValidation("unavailable")

        if response.status_code == 404:
            return RepositoryValidation("not_found")
        if response.status_code in {401, 403, 429} or response.is_server_error:
            log.warning("GitHub API validation returned %s", response.status_code)
            return RepositoryValidation("unavailable")
        if response.is_error:
            return RepositoryValidation("unavailable")

        try:
            data = response.json()
        except ValueError:
            log.warning("GitHub API returned invalid JSON")
            return RepositoryValidation("unavailable")
        if not isinstance(data, dict):
            return RepositoryValidation("unavailable")
        if data.get("private"):
            return RepositoryValidation("private")
        return RepositoryValidation(
            "ok",
            GithubRepository(
                owner=repository.owner,
                repo=repository.repo,
                url=repository.url,
                full_name=data.get("full_name") or repository.full_name,
                description=data.get("description"),
            ),
        )

    async def download_current_archive(self, owner: str, repo: str) -> ArchiveDownload:
        if not _NAME_RE.fullmatch(owner) or not _NAME_RE.fullmatch(repo):
            return ArchiveDownload("unavailable")
        path: Path | None = None
        completed = False
        try:
            with tempfile.NamedTemporaryFile(
                prefix="github_", suffix=".zip", delete=False
            ) as file:
                path = Path(file.name)
                async with (
                    httpx.AsyncClient(
                        timeout=httpx.Timeout(
                            connect=15.0, read=90.0, write=30.0, pool=15.0
                        ),
                        follow_redirects=True,
                    ) as client,
                    client.stream(
                        "GET",
                        f"{_API_ROOT}/{owner}/{repo}/zipball",
                        headers={"Accept": "application/vnd.github+json"},
                    ) as response,
                ):
                    if response.status_code == 404:
                        return ArchiveDownload("not_found")
                    if response.status_code in {401, 403, 429} or response.is_error:
                        log.warning(
                            "GitHub archive returned %s for %s/%s",
                            response.status_code,
                            owner,
                            repo,
                        )
                        return ArchiveDownload("unavailable")
                    length = response.headers.get("content-length")
                    if length and int(length) > self.max_archive_size:
                        return ArchiveDownload("too_large")
                    downloaded = 0
                    async for chunk in response.aiter_bytes():
                        downloaded += len(chunk)
                        if downloaded > self.max_archive_size:
                            return ArchiveDownload("too_large")
                        await asyncio.to_thread(file.write, chunk)
            completed = True
            return ArchiveDownload("ok", path)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            log.warning(
                "GitHub archive download failed for %s/%s: %s", owner, repo, exc
            )
            return ArchiveDownload("unavailable")
        finally:
            if path and path.exists() and not completed:
                path.unlink(missing_ok=True)
