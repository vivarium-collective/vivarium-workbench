#!/usr/bin/env bash

set -e

STACK=${1:-smsvpctest}

export AWS_PROFILE=stanford-sso
export AWS_DEFAULT_REGION=us-gov-west-1

kubeconf=/Users/alexanderpatrie/.kube/kube_stanford_test.yml

if [[ "$STACK" == "smscdk" ]]; then
  kubeconf=/Users/alexanderpatrie/.kube/kube_stanford.yml
fi

export KUBECONFIG=$kubeconf