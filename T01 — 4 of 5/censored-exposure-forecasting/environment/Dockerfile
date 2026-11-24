FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

# The build context is the task's environment/ directory, so COPY sources below
# are relative to it (data/, baseline/, analysis/).

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Harness needs git (agent diff capture) and tmux (Terminus-2 drives a tmux session);
# both must be present offline inside the graded container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git tmux ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Numeric stack on the STDLIB path: the verifier runs `python3 -I -S`, which
# suppresses site-packages, so packages placed only in site-packages are invisible to it.
# Installing to sysconfig's stdlib dir also keeps them importable by the agent's normal
# python3 (stdlib is always on sys.path). pytest is NOT installed here - it is a
# test-only dependency and belongs only in tests/Dockerfile.
RUN STDLIB=$(python3 -c "import sysconfig;print(sysconfig.get_paths()['stdlib'])") \
    && python3 -m pip install --no-cache-dir --no-compile --target "$STDLIB" \
       numpy==2.2.3 scipy==1.15.2 pandas==2.2.3
       
WORKDIR /app

# Visible input data for model fitting.
COPY data/ /app/data/
# The naive baseline predictor and the stale analysis memo.
COPY baseline/ /app/baseline/
COPY analysis/ /app/analysis/
# Task instructions for the agent working directory.
COPY instruction.md /app/instruction.md

# Submission landing zone, and the agent owns the workdir.
RUN mkdir -p /app/submission

# Bytecode compiled during the build embeds source paths and survives a source delete.
RUN find / -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

# Drop to a fixed numeric non-root user as the final runtime identity.
RUN u=$(getent passwd 1000 | cut -d: -f1); [ -n "$u" ] && userdel -r "$u" 2>/dev/null; useradd -m -u 1000 agent
RUN chown -R 1000:1000 /app
# >>> pipeline: harness paths owned by the agent user (regenerated before every check) >>>
RUN mkdir -p /solution /logs/agent /app && chown 1000 /solution /logs/agent && chown -R -h 1000 /app && chmod u+rwx /solution /logs/agent /app
# <<< pipeline: harness paths <<<
USER 1000:1000
