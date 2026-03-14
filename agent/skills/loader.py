import os
import re
import logging
from dataclasses import dataclass
from typing import Optional
import yaml

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    path: str
    source: str = "project"
    content: Optional[str] = None


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SKILLS_DIR = os.path.join(PROJECT_ROOT, "skills")
GLOBAL_SKILLS_DIR = os.path.expanduser("~/.agent/skills")


def parse_frontmatter(content: str) -> dict:
    frontmatter_pattern = r"^---\s*\n(.*?)\n---"
    match = re.match(frontmatter_pattern, content, re.DOTALL)
    if not match:
        return {}
    yaml_content = match.group(1)
    try:
        return yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError:
        return {}


def load_skill_metadata(skill_path: str, source: str = "project") -> Optional[dict]:
    skill_md_path = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        return None
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
        frontmatter = parse_frontmatter(content)
        if not frontmatter or "name" not in frontmatter:
            return None
        return {
            "name": frontmatter.get("name", ""),
            "description": frontmatter.get("description", ""),
            "path": skill_path,
            "source": source,
        }
    except Exception:
        return None


def load_skill_content(skill_path: str) -> Optional[str]:
    skill_md_path = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        return None
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
        frontmatter_pattern = r"^---\s*\n.*?\n---\s*\n"
        body = re.sub(frontmatter_pattern, "", content, count=1, flags=re.DOTALL)
        return body.strip()
    except Exception:
        return None


def get_available_skills() -> list[dict]:
    skills = []
    seen_names = set()

    for skills_dir, source in [(SKILLS_DIR, "project"), (GLOBAL_SKILLS_DIR, "global")]:
        if not os.path.isdir(skills_dir):
            continue
        for entry in os.listdir(skills_dir):
            skill_path = os.path.join(skills_dir, entry)
            if os.path.isdir(skill_path):
                metadata = load_skill_metadata(skill_path, source)
                if metadata:
                    name = metadata["name"]
                    if name not in seen_names:
                        seen_names.add(name)
                        skills.append(metadata)
                        logger.info(
                            f"[Skills] Loaded skill '{name}' from {source}: {skill_path}"
                        )
                    else:
                        logger.debug(
                            f"[Skills] Skipped duplicate skill '{name}' from {source}"
                        )

    return skills


def get_skill(name: str) -> Optional[Skill]:
    for skills_dir, source in [(SKILLS_DIR, "project"), (GLOBAL_SKILLS_DIR, "global")]:
        skill_path = os.path.join(skills_dir, name)
        if os.path.isdir(skill_path):
            metadata = load_skill_metadata(skill_path, source)
            if metadata:
                content = load_skill_content(skill_path)
                logger.info(f"[Skills] Loaded skill '{name}' from {source}")
                return Skill(
                    name=metadata["name"],
                    description=metadata["description"],
                    path=metadata["path"],
                    source=source,
                    content=content,
                )
    logger.warning(f"[Skills] Skill '{name}' not found in project or global directory")
    return None


def generate_skills_prompt() -> str:
    skills = get_available_skills()
    if not skills:
        return ""
    lines = ["<available_skills>"]
    for skill in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{skill['name']}</name>")
        lines.append(f"    <description>{skill['description']}</description>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def generate_skills_context() -> str:
    skills = get_available_skills()
    if not skills:
        return ""
    lines = ["\n\n## 可用技能\n\n"]
    for skill in skills:
        lines.append(f"- **{skill['name']}**: {skill['description']}\n")
    return "".join(lines)
