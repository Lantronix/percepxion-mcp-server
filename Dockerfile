FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# NOTE: A non-root USER (e.g. appuser) was considered as part of the security
# hardening pass but deferred. update_firmware_by_smart_group reads firmware
# files from PERCEPXION_FIRMWARE_DIR, which is bind-mounted at deploy time.
# Whether that mount is readable by a non-root container UID is a deployment/ops
# question that can't be resolved from the app-level RBAC docs alone. Add a USER
# directive once the production bind-mount permissions for PERCEPXION_FIRMWARE_DIR
# are confirmed readable by the non-root user.
EXPOSE 8765
CMD ["python", "percepxion_mcp.py"]
