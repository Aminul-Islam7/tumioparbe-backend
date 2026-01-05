#!/bin/bash
# ============================================
# Payment Reconciliation Cron Script
# ============================================
# This script reconciles stale bKash payments.
# Run this every 5-10 minutes via cron job.
#
# Cron Schedule Examples:
#   */5 * * * *   - Every 5 minutes
#   */10 * * * *  - Every 10 minutes
# ============================================

# Set the path to your Django project
PROJECT_DIR="/home/tumiopa1/public_html/backend"

# Activate virtual environment if you have one
# source "$PROJECT_DIR/venv/bin/activate"

# Change to project directory
cd "$PROJECT_DIR" || exit 1

# Run the reconciliation command
# The output will be sent to the cron email you configured
python manage.py reconcile_payments

# Exit with the same status as the Django command
exit $?
