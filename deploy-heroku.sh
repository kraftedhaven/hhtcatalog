#!/bin/bash
# Heroku Production Deployment Script for HHT Catalog
# Usage: bash deploy-heroku.sh

set -e

APP_NAME="hht-catalog"

echo "🚀 Deploying HHT Catalog to Heroku..."
echo "App: $APP_NAME"

# 1. Verify Heroku login
echo "✓ Checking Heroku CLI..."
heroku auth:whoami --app $APP_NAME > /dev/null || { echo "❌ Not logged in to Heroku. Run: heroku login"; exit 1; }

# 2. Push main branch to Heroku
echo "✓ Pushing main branch to Heroku..."
git push heroku main

# 3. Wait for build to complete
echo "✓ Waiting for build to complete (usually 2-3 minutes)..."
sleep 5

# 4. Scale dynos
echo "✓ Scaling dynos..."
heroku ps:scale web=1 worker=1 --app $APP_NAME

# 5. Verify deployment
echo "✓ Checking health endpoint..."
sleep 10
HEALTH=$(curl -s https://$APP_NAME.herokuapp.com/health | grep -o '"status":"ok"' || echo "")
if [ -z "$HEALTH" ]; then
    echo "⚠️  Health check pending... Check logs with: heroku logs --tail --app $APP_NAME"
else
    echo "✅ API is healthy"
fi

# 6. Show status
echo "✓ Deployment status:"
heroku ps --app $APP_NAME

echo "✅ Deployment complete!"
echo ""
echo "Next steps:"
echo "  - Monitor logs: heroku logs --tail --app $APP_NAME"
echo "  - Check worker: heroku logs --dyno worker.1 --tail --app $APP_NAME"
echo "  - View config: heroku config --app $APP_NAME"
echo ""
