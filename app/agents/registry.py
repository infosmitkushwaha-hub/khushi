"""All agents the scheduler and Telegram bot know about.

Each agent module exposes: NAME, DEFAULT_JOB, JOBS {name: async fn}, schedule() -> [(job, "HH:MM", days)] and
repeating() -> {job: seconds}.
"""

from app.agents.khushi import agent as khushi

AGENTS = {"khushi": khushi}
