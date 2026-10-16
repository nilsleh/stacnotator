# Python base image that installs packages through a package-feed proxy.
# Used as the tiler's PYTHON_IMAGE where direct PyPI access is blocked:
#   docker build -t stacnotator-pybase:proxy \
#     --build-arg PIP_INDEX_URL=https://packagefeedproxy.microsoft.io/pypi/simple/ \
#     - < deployment/local/pip-proxy-base.Dockerfile
#   then set TILER_PYTHON_IMAGE=stacnotator-pybase:proxy for docker compose.
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim
FROM ${PYTHON_IMAGE}
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
