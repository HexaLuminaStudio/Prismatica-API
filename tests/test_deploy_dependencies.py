"""生产镜像运行时依赖一致性测试。"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _normalizePackageName(requirement: str) -> str:
    """提取并规范化 PEP 508 依赖名。"""
    match = re.match(r"[A-Za-z0-9_.-]+", requirement)
    assert match is not None, f"无法解析依赖声明: {requirement}"
    return re.sub(r"[-_.]+", "-", match.group(0)).lower()


def testDockerRuntimeDependenciesMatchPyproject():
    """Dockerfile 手工安装清单必须覆盖 pyproject 的全部运行时依赖。"""
    pyprojectPath = PROJECT_ROOT / "pyproject.toml"
    dockerfilePath = PROJECT_ROOT / "deploy" / "Dockerfile"

    with pyprojectPath.open("rb") as file:
        projectDependencies = tomllib.load(file)["project"]["dependencies"]

    dockerfile = dockerfilePath.read_text(encoding="utf-8")
    installBlockMatch = re.search(
        r"&& pip install \\\n(?P<requirements>.*?)(?:\r?\n){2}",
        dockerfile,
        flags=re.DOTALL,
    )
    assert installBlockMatch is not None, "未找到 Dockerfile 的 pip install 依赖清单"

    dockerRequirements = re.findall(r'"([A-Za-z0-9_.-]+(?:[^"\\]*))"', installBlockMatch.group("requirements"))
    projectNames = {_normalizePackageName(requirement) for requirement in projectDependencies}
    dockerNames = {_normalizePackageName(requirement) for requirement in dockerRequirements}

    missingNames = sorted(projectNames - dockerNames)
    assert not missingNames, f"Dockerfile 缺少运行时依赖: {', '.join(missingNames)}"
