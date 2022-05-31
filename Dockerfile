FROM python:3-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG DEBCONF_NOWARNINGS="yes"

WORKDIR /app

#RUN apt-get update && apt-get install -y libxml2-dev libxslt-dev python3-lxml python3-dev build-essential
RUN apt-get update && apt-get install -y python3-lxml python3-dev build-essential

#RUN cp /etc/apt/sources.list /etc/apt/sources.list~ && sed -Ei 's/^# deb-src /deb-src /' /etc/apt/sources.list
#ADD requirements.txt .
COPY . .

RUN pip install --upgrade pip && STATIC_DEPS=true CFLAGS="-O0" pip install lxml
#RUN pip install --no-deps pyiso 
#RUN pip install -r requirements.txt
RUN pip install .  
#https://github.com/WattTime/pyiso/issues/197#issuecomment-524953136

CMD /bin/bash