locals {
  region      = data.aws_region.current.region
  secret_name = coalesce(var.secret_name, "${var.name}/registry-env")
  # Conditional, not `||`: Terraform 1.11 evaluates both sides of `||`, so
  # trimspace(tostring(null)) would fail when add_ons_ready is unset.
  add_ons_ready_valid = var.add_ons_ready == null ? true : trimspace(tostring(var.add_ons_ready)) != ""

  oidc_provider = trimprefix(data.aws_iam_openid_connect_provider.this.url, "https://")

  vpc_associated_cidrs = [
    for assoc in data.aws_vpc.this.cidr_block_associations : assoc.cidr_block
    if assoc.state == "associated" && assoc.cidr_block != ""
  ]
  vpc_cidrs = join(",", length(local.vpc_associated_cidrs) > 0 ? local.vpc_associated_cidrs : [data.aws_vpc.this.cidr_block])
}
