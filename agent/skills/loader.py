import os
import re
from dataclasses import dataclass
from typing import Optional
import yaml


@dataclass
class Skill:
    name: str
    description: str
    path: str
    content: Optional[str] = None


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SKILLS_DIR = os.path.join(PROJECT_ROOT, "skills")


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


def load_skill_metadata(skill_path: str) -> Optional[dict]:
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
    if not os.path.isdir(SKILLS_DIR):
        return []
    skills = []
    for entry in os.listdir(SKILLS_DIR):
        skill_path = os.path.join(SKILLS_DIR, entry)
        if os.path.isdir(skill_path):
            metadata = load_skill_metadata(skill_path)
            if metadata:
                skills.append(metadata)
    return skills


def get_skill(name: str) -> Optional[Skill]:
    skill_path = os.path.join(SKILLS_DIR, name)
    if not os.path.isdir(skill_path):
        return None
    metadata = load_skill_metadata(skill_path)
    if not metadata:
        return None
    content = load_skill_content(skill_path)
    return Skill(
        name=metadata["name"],
        description=metadata["description"],
        path=metadata["path"],
        content=content,
    )


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
