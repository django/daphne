#! /bin/bash
find . -name "*.pyc" -delete

docker run --rm \
--mount type=bind,source=`pwd`,target=/src \
daphne
