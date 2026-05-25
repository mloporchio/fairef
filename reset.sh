#!/bin/bash

echo "Forcing Docker shutdown and cleanup..."
docker compose -f docker-compose.generated.toxiproxy.yml down # Stop Docker
systemctl --user stop docker
echo "Forcing shutdown of all listeners..."
pkill -f modelCreatorApprove.js
echo "Removing Docker and log files..."
rm *.log *.yml
rm -rf generated
echo "Docker is restarting..."
systemctl --user start docker
echo "Done!"