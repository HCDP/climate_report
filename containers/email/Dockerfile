FROM ghcr.io/hcdp/task-base:latest
LABEL org.opencontainers.image.source="https://github.com/hcdp/climate_report"
LABEL org.opencontainers.image.description="Base image for climate report workflow."

RUN apt update

RUN pip install --upgrade pip
RUN python3 -m pip install "urllib3>=2.0.0"


RUN mkdir -p /workspace/code

ADD /envs/prod.env /workspace/envs

ADD /code/email /workspace/code
# Add the task wrapper script.
ADD /containers/email/task.sh /workspace/task.sh

WORKDIR /workspace
CMD [ "/bin/bash", "task.sh" ]
