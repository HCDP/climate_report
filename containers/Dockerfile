FROM ghcr.io/hcdp/task-base:latest
LABEL org.opencontainers.image.source="https://github.com/hcdp/climate_report"
LABEL org.opencontainers.image.description="Base image for climate report workflow."

RUN apt update
RUN apt install gdal-bin libgdal-dev -y

RUN pip install --upgrade pip

RUN python3 -m pip install rasterio
RUN python3 -m pip install numpy
RUN python3 -m pip install pandas
RUN python3 -m pip install geopandas
RUN python3 -m pip install rasterio
RUN python3 -m pip install rasterstats
RUN python3 -m pip install python-dateutil
RUN python3 -m pip install pytz
RUN python3 -m pip install matplotlib


RUN mkdir -p /workspace
WORKDIR /workspace
RUN wget -nv https://api.hcdp.ikewai.org/files/download/workflow_data/climate_report/dependencies.tar.gz
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

ADD /code /workspace/code
# Get the container's upload config as specified in the code repo.
ADD /containers/configs/upload.json /sync/config.json
# Add the task wrapper script.
ADD /containers/task.sh /workspace/task.sh

WORKDIR /workspace
# This container is intended to run the task wrapper at launch, unless otherwise specified at run-time.
CMD [ "/bin/bash", "task.sh" ]
