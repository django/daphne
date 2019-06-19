FROM python:3.7

RUN mkdir /src
WORKDIR /src
ADD . /src/

RUN pip install -e .[tests]

CMD ["pytest"]
