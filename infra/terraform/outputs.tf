output "instance_id" {
  value = aws_instance.actioninbox.id
}

output "availability_zone" {
  value = aws_instance.actioninbox.availability_zone
}

output "elastic_ip" {
  value = aws_eip.actioninbox.public_ip
}

output "public_url" {
  value = "http://${aws_eip.actioninbox.public_ip}"
}
