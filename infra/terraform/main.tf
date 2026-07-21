provider "aws" {
  profile = var.aws_profile != "" ? var.aws_profile : null
  region  = var.aws_region

  default_tags {
    tags = {
      Project   = "ActionInbox"
      ManagedBy = "Terraform"
      Purpose   = "BuildWeekEmergencyDeployment"
    }
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "actioninbox" {
  name        = "${var.name_prefix}-sg"
  description = "ActionInbox emergency web and restricted administration access"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS-ready"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH from deployment workstation only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  egress {
    description = "Required package, image, and OpenAI egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.name_prefix}-sg"
  }
}

resource "aws_instance" "actioninbox" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = sort(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids      = [aws_security_group.actioninbox.id]
  associate_public_ip_address = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 16
    encrypted             = true
    delete_on_termination = true
  }

  user_data = <<-CLOUD_INIT
    #!/bin/bash
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y docker.io docker-compose-v2 git curl
    systemctl enable --now docker
    usermod -aG docker ubuntu
    install -d -m 0750 -o ubuntu -g ubuntu /opt/actioninbox
    touch /var/lib/cloud/instance/actioninbox-bootstrap-complete
  CLOUD_INIT

  tags = {
    Name = "${var.name_prefix}-ec2"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_eip" "actioninbox" {
  domain = "vpc"

  tags = {
    Name = "${var.name_prefix}-eip"
  }
}

resource "aws_eip_association" "actioninbox" {
  allocation_id = aws_eip.actioninbox.id
  instance_id   = aws_instance.actioninbox.id
}
