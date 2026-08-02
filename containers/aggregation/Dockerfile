FROM ghcr.io/hcdp/task-base:latest
LABEL org.opencontainers.image.source="https://github.com/hcdp/climate_report"
LABEL org.opencontainers.image.description="Base image for climate report workflow."

RUN apt update
RUN apt install gdal-bin libgdal-dev -y

RUN pip install --upgrade pip

RUN python3 -m pip install \
    rasterio \
    numpy \
    pandas \
    geopandas \
    rasterstats \
    python-dateutil \
    pytz \
    "urllib3>=2.0.0"



RUN mkdir -p /workspace
WORKDIR /workspace
RUN wget -4 -nv --connect-timeout=5 --tries=3 https://api.hcdp.ikewai.org/files/download/workflow_data/climate_report/dependencies.tar.gz
RUN tar -xf dependencies.tar.gz
RUN rm dependencies.tar.gz


RUN mkdir -p /workspace/envs
RUN mkdir -p /workspace/code
RUN mkdir -p /workspace/output
RUN mkdir -p /workspace/dependencies
RUN mkdir -p /sync

RUN wget https://raw.githubusercontent.com/ikewai/data-uploader/main/upload.py -O /sync/upload.py

ADD /tools /sync
ADD /envs/prod.env /workspace/envs

ADD /code/aggregation /workspace/code
# Get the container's upload config as specified in the code repo.
ADD /containers/aggregation/configs/upload.json /sync/config.json
# Add the task wrapper script.
ADD /containers/aggregation/task.sh /workspace/task.sh

WORKDIR /workspace
CMD [ "/bin/bash", "task.sh" ]
