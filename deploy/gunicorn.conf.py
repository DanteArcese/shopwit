import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("WEB_CONCURRENCY", 3))
timeout = 30
graceful_timeout = 20
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
# No client IP, user agent or query string is ever logged.
access_log_format = '%(t)s %(m)s %(U)s %(s)s %(B)s %(M)sms'
