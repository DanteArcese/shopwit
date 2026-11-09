FROM postgres:17

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres -m 700 /backups

# Debian's packaged rclone is too old for R2 (first upload fails with 501), so
# install a pinned upstream release, verified against a checksum pinned here
# (from the release's SHA256SUMS). Bump both together.
ARG RCLONE_RELEASE=v1.76.0
ARG RCLONE_RELEASE_SHA256=b00f00b3266c3764413afe599d3ec316f6f43b3e0ea8a0bc1d186d2133e46df7
RUN cd /tmp \
    && curl -fsSLO --retry 5 --retry-all-errors --retry-delay 5 "https://github.com/rclone/rclone/releases/download/${RCLONE_RELEASE}/rclone-${RCLONE_RELEASE}-linux-amd64.zip" \
    && echo "${RCLONE_RELEASE_SHA256}  rclone-${RCLONE_RELEASE}-linux-amd64.zip" | sha256sum -c - \
    && unzip -j "rclone-${RCLONE_RELEASE}-linux-amd64.zip" "rclone-${RCLONE_RELEASE}-linux-amd64/rclone" -d /usr/local/bin \
    && chmod 755 /usr/local/bin/rclone \
    && rm -f /tmp/rclone* \
    && rclone version

COPY backup.sh /usr/local/bin/backup.sh
RUN chmod 755 /usr/local/bin/backup.sh

USER postgres
ENTRYPOINT ["/usr/local/bin/backup.sh"]
CMD ["loop"]
