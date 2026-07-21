variable "aws_profile" {
  description = "AWS CLI profile used for this deployment."
  type        = string
  default     = "actioninbox-deploy"
}

variable "aws_region" {
  description = "AWS region for the emergency deployment."
  type        = string
  default     = "eu-north-1"
}

variable "name_prefix" {
  description = "Unique prefix for ActionInbox emergency resources."
  type        = string
  default     = "actioninbox-emergency-20260721"
}

variable "ssh_cidr" {
  description = "Single trusted public IPv4 CIDR allowed to use SSH."
  type        = string

  validation {
    condition     = can(cidrhost(var.ssh_cidr, 0)) && tonumber(split("/", var.ssh_cidr)[1]) == 32
    error_message = "ssh_cidr must be a single IPv4 /32 CIDR."
  }
}

variable "instance_type" {
  description = "Smallest instance selected for Docker, MySQL, and Nginx together."
  type        = string
  default     = "t3.small"
}
