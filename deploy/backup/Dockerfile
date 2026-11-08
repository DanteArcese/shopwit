FROM postgres:17

RUN apt-get update \
    && apt-get install -y --no-install-recommends rclone curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres -m 700 /backups

COPY backup.sh /usr/local/bin/backup.sh
RUN chmod 755 /usr/local/bin/backup.sh

USER postgres
ENTRYPOINT ["/usr/local/bin/backup.sh"]
CMD ["loop"]
