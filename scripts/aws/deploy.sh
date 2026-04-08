#!/bin/bash
# ABOUTME: Provisions an EC2 instance and deploys luthien-proxy with Docker Compose.
# ABOUTME: Run from a machine with AWS CLI configured. Outputs the proxy URL when done.
#
# Usage:
#   ./scripts/aws/deploy.sh [OPTIONS]
#
# Options:
#   --region REGION        AWS region (default: eu-central-1)
#   --instance-type TYPE   EC2 instance type (default: t3.small)
#   --key-name NAME        Existing SSH key pair name (required)
#   --repo URL             Git repo URL to clone (default: current origin)
#   --branch BRANCH        Branch to deploy (default: main)
#   --help                 Show this help
#
# Prerequisites:
#   - AWS CLI configured and authenticated
#   - An SSH key pair already created in the target AWS region
#     (create one: aws ec2 create-key-pair --key-name luthien --query 'KeyMaterial' --output text > luthien.pem)

set -euo pipefail

# --- Defaults ---
REGION="eu-central-1"
INSTANCE_TYPE="t3.small"
KEY_NAME=""
REPO_URL=""
BRANCH="main"
SG_NAME="luthien-proxy-sg"
INSTANCE_NAME="luthien-proxy"

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --region) REGION="$2"; shift 2 ;;
        --instance-type) INSTANCE_TYPE="$2"; shift 2 ;;
        --key-name) KEY_NAME="$2"; shift 2 ;;
        --repo) REPO_URL="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --help)
            head -20 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# --- Validate ---
if ! command -v aws &>/dev/null; then
    echo "Error: AWS CLI not found. Install it first."
    exit 1
fi

if ! aws sts get-caller-identity &>/dev/null; then
    echo "Error: AWS CLI not authenticated. Run 'aws configure' or set credentials."
    exit 1
fi

if [[ -z "$KEY_NAME" ]]; then
    echo "Error: --key-name is required."
    echo ""
    echo "List existing key pairs:"
    echo "  aws ec2 describe-key-pairs --region $REGION --query 'KeyPairs[].KeyName' --output text"
    echo ""
    echo "Or create one:"
    echo "  aws ec2 create-key-pair --region $REGION --key-name luthien --query 'KeyMaterial' --output text > luthien.pem"
    echo "  chmod 400 luthien.pem"
    exit 1
fi

# Auto-detect repo URL from git remote if not specified
if [[ -z "$REPO_URL" ]]; then
    REPO_URL=$(git remote get-url origin 2>/dev/null || echo "")
    if [[ -z "$REPO_URL" ]]; then
        echo "Error: --repo URL required (not in a git repo)."
        exit 1
    fi
    # Convert SSH URL to HTTPS for the EC2 instance
    if [[ "$REPO_URL" == git@* ]]; then
        REPO_URL=$(echo "$REPO_URL" | sed 's|git@github.com:|https://github.com/|')
    fi
    echo "Using repo: $REPO_URL"
fi

echo "=== Luthien Proxy AWS Deployment ==="
echo "Region:   $REGION"
echo "Instance: $INSTANCE_TYPE"
echo "Key:      $KEY_NAME"
echo "Repo:     $REPO_URL"
echo "Branch:   $BRANCH"
echo ""

# --- Find latest Ubuntu 24.04 AMI ---
echo "Finding latest Ubuntu 24.04 AMI..."
AMI_ID=$(aws ec2 describe-images \
    --region "$REGION" \
    --owners 099720109477 \
    --filters \
        "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" \
        "Name=state,Values=available" \
    --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
    --output text)

if [[ -z "$AMI_ID" || "$AMI_ID" == "None" ]]; then
    echo "Error: Could not find Ubuntu 24.04 AMI in $REGION"
    exit 1
fi
echo "AMI: $AMI_ID"

# --- Create or reuse security group ---
echo "Setting up security group..."
VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" \
    --filters "Name=is-default,Values=true" \
    --query 'Vpcs[0].VpcId' --output text)

SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then
    SG_ID=$(aws ec2 create-security-group --region "$REGION" \
        --group-name "$SG_NAME" \
        --description "Luthien proxy - SSH + gateway" \
        --vpc-id "$VPC_ID" \
        --query 'GroupId' --output text)

    # SSH access
    aws ec2 authorize-security-group-ingress --region "$REGION" \
        --group-id "$SG_ID" --protocol tcp --port 22 --cidr 0.0.0.0/0

    # Gateway port
    aws ec2 authorize-security-group-ingress --region "$REGION" \
        --group-id "$SG_ID" --protocol tcp --port 8000 --cidr 0.0.0.0/0

    echo "Created security group: $SG_ID"
else
    echo "Reusing security group: $SG_ID"
fi

# --- Generate secrets ---
PROXY_API_KEY="sk-luthien-$(openssl rand -hex 16)"
ADMIN_API_KEY="admin-$(openssl rand -hex 16)"
POSTGRES_PASSWORD="pg-$(openssl rand -hex 16)"

# --- Build user-data script ---
USERDATA_FILE=$(mktemp /tmp/luthien-userdata.XXXXXX)
trap 'rm -f "$USERDATA_FILE"' EXIT

cat > "$USERDATA_FILE" <<EOF
#!/bin/bash
set -euo pipefail
exec > /var/log/luthien-setup.log 2>&1

echo "=== Luthien setup starting ==="

# Install Docker
apt-get update -y
apt-get install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \$(. /etc/os-release && echo \$VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# Clone repo
cd /home/ubuntu
git clone --branch ${BRANCH} ${REPO_URL} luthien-proxy
cd luthien-proxy

# Fix: remove :ro from src volume mount so hatch-vcs can write _version.py at startup
sed -i 's|./src:/app/src:ro|./src:/app/src|' docker-compose.yaml

# Generate version file (not in git, needed by hatch-vcs)
echo '__version__ = "0.0.0-dev"' > src/luthien_proxy/_version.py

# Write .env
cat > .env <<'ENVFILE'
ANTHROPIC_API_KEY=
PROXY_API_KEY=${PROXY_API_KEY}
ADMIN_API_KEY=${ADMIN_API_KEY}
AUTH_MODE=both
POLICY_SOURCE=db-fallback-file
POLICY_CONFIG=/app/config/policy_config.yaml
LOCALHOST_AUTH_BYPASS=false
POSTGRES_USER=luthien
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=luthien_control
DATABASE_URL=postgresql://luthien:${POSTGRES_PASSWORD}@db:5432/luthien_control
REDIS_URL=redis://redis:6379
GATEWAY_PORT=8000
OTEL_ENABLED=false
ENABLE_REQUEST_LOGGING=true
USAGE_TELEMETRY=false
ENVFILE

# Fix ownership
chown -R ubuntu:ubuntu /home/ubuntu/luthien-proxy

# Start services (build from source — works for forks and custom branches)
docker compose build
docker compose up -d

echo "=== Luthien setup complete ==="
EOF

# --- Launch instance ---
echo "Launching EC2 instance..."
INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --user-data "file://$USERDATA_FILE" \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME}]" \
    --query 'Instances[0].InstanceId' --output text)

echo "Instance: $INSTANCE_ID"
echo "Waiting for instance to be running..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

# Get public IP
PUBLIC_IP=$(aws ec2 describe-instances --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

echo ""
echo "=== Deployment initiated ==="
echo ""
echo "Instance ID:  $INSTANCE_ID"
echo "Public IP:    $PUBLIC_IP"
echo ""
echo "The instance is installing Docker and starting the proxy."
echo "This takes 2-3 minutes. Monitor progress with:"
echo ""
echo "  ssh -i <your-key>.pem ubuntu@$PUBLIC_IP tail -f /var/log/luthien-setup.log"
echo ""
echo "Once ready, the proxy will be at:"
echo ""
echo "  http://$PUBLIC_IP:8000"
echo ""
echo "=== Credentials (save these!) ==="
echo ""
echo "  PROXY_API_KEY:  $PROXY_API_KEY"
echo "  ADMIN_API_KEY:  $ADMIN_API_KEY"
echo "  POSTGRES_PASS:  $POSTGRES_PASSWORD"
echo ""
echo "Admin UI:     http://$PUBLIC_IP:8000/policy-config"
echo "History:      http://$PUBLIC_IP:8000/history"
echo "Health check: curl http://$PUBLIC_IP:8000/health"
echo ""
echo "=== Connect team members ==="
echo ""
echo "Team members run:"
echo "  uv tool install luthien-cli"
echo "  luthien connect http://$PUBLIC_IP:8000"
echo "  luthien claude"
echo ""
echo "(luthien connect doesn't exist yet — for now, manually set"
echo " ANTHROPIC_BASE_URL=http://$PUBLIC_IP:8000 when running claude)"
echo ""
echo "=== Management ==="
echo ""
echo "  ssh -i <your-key>.pem ubuntu@$PUBLIC_IP"
echo "  cd luthien-proxy"
echo "  docker compose logs -f gateway    # view logs"
echo "  docker compose restart gateway    # restart"
echo "  git pull && docker compose up -d  # update"
