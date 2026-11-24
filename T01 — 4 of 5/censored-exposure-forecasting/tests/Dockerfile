FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

# Verifier image. Built from tests/ as its context; environment/Dockerfile is NOT used here.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends util-linux ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The suite runs under `python3 -I -S`, which suppresses site-packages, so the numeric
# stack the grader imports and pytest must live on the stdlib path.
# ENVIRONMENT PARITY: this pin list MUST stay identical to environment/Dockerfile's - the
# agent develops against that image, so a submission that imports pandas (preinstalled
# there) must also import here. A drift between the two scores valid solutions 0.
RUN STDLIB=$(python3 -c "import sysconfig;print(sysconfig.get_paths()['stdlib'])") \
    && python3 -m pip install --no-cache-dir --no-compile --target "$STDLIB" \
       pytest==8.4.1 pytest-json-ctrf==0.6.1 numpy==2.2.3 scipy==1.15.2 pandas==2.2.3

# The agent user must exist at the same uid as the agent image, or the prelude fails closed.
# python:*-slim ships no uid-1000 user, so create it.
RUN useradd -m -u 1000 agent

# Graded artifacts land at their original absolute path; harbor does not create the parent.
RUN mkdir -p /app/submission /logs/verifier

# Held-out truth generator, simulator and suite. Root-only so the agent cannot read them.
COPY .truth/truth_model.py /tests/.truth/truth_model.py
COPY .truth/heldout.py /tests/.truth/heldout.py
COPY test_outputs.py /tests/test_outputs.py
COPY test.sh /tests/test.sh

RUN chown -R 0:0 /tests && chmod -R go-rwx /tests
