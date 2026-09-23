"""
setup.py

Packaging metadata for the Autonomous Financial Research Agent project.
Lets the project be installed in editable mode (`pip install -e .`) so
`import agent`, `import tools`, etc. work from anywhere without manual
sys.path manipulation -- most of this codebase currently relies on being
run from the project root, and this makes that more robust.
"""

from setuptools import find_packages, setup

with open("requirements.txt", encoding="utf-8") as f:
    requirements = [
        line.strip()
        for line in f
        if line.strip() and not line.strip().startswith("#")
    ]

setup(
    name="autonomous-financial-research-agent",
    version="0.1.0",
    description=(
        "An autonomous AI agent that researches public companies the way "
        "a junior financial analyst would: forming a research plan, "
        "gathering data from SEC filings, financial APIs, news, and web "
        "search, resolving conflicts across sources, and producing a "
        "structured investment research report."
    ),
    packages=find_packages(
        include=[
            "agent",
            "agent.*",
            "tools",
            "tools.*",
            "memory",
            "memory.*",
            "synthesis",
            "synthesis.*",
            "evaluation",
            "evaluation.*",
            "config",
            "config.*",
        ]
    ),
    python_requires=">=3.10",
    install_requires=requirements,
    include_package_data=True,
    package_data={"tools": ["schemas/*.json"]},
)
