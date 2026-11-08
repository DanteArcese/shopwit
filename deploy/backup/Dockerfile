FROM postgres:17

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres -m 700 /backups

# Debian's packaged rclone is too old for R2 (first upload fails with 501), so
# install a pinned upstream release, verified against the release's SHA256SUMS.
ARG RCLONE_VERSION=v1.76.0
RUN cd /tmp \
    && curl -fsSLO "https://github.com/rclone/rclone/releases/download/${RCLONE_VERSION}/rclone-${RCLONE_VERSION}-linux-amd64.zip" \
    && curl -fsSL "https://github.com/rclone/rclone/releases/download/${RCLONE_VERSION}/SHA256SUMS" \
        | grep " rclone-${RCLONE_VERSION}-linux-amd64.zip\$" > rclone.sha256 \
    && sha256sum -c rclone.sha256 \
    && unzip -j "rclone-${RCLONE_VERSION}-linux-amd64.zip" "rclone-${RCLONE_VERSION}-linux-amd64/rclone" -d /usr/local/bin \
    && chmod 755 /usr/local/bin/rclone \
    && rm -f /tmp/rclone* \
    && rclone version

COPY backup.sh /usr/local/bin/backup.sh
RUN chmod 755 /usr/local/bin/backup.sh

USER postgres
ENTRYPOINT ["/usr/local/bin/backup.sh"]
CMD ["loop"]
