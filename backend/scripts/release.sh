#!/bin/sh
# The one-off step to run when a new version is released (a Compose service, or a Cloud Run job): bring the database
# schema up to date, then make sure the first admin exists. Safe to run again: both parts do nothing when there is
# nothing to do (the admin's password is never overwritten).
set -e
cd "$(dirname "$0")/.."
alembic upgrade head
python scripts/seed_admin.py
