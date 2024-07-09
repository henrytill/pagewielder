#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PYTHON=python3
MYPY=mypy
VENV=env

USE_VENV=0
VERBOSE=0

activate() {
  if [ $USE_VENV -eq 1 ]; then
    source "${VENV}/bin/activate"
  fi
}

create_env() {
  USE_VENV=1
  $PYTHON -m venv $VENV
  activate
  which $PYTHON
  $PYTHON -m pip install --upgrade pip
  $PYTHON -m pip install -e .[dev]
}

check() {
  activate
  $MYPY pagewielder
}

lint() {
  activate
  $PYTHON -m flake8 --config .flake8
  $PYTHON -m pylint pagewielder
}

clean() {
  rm -rf $VENV
  rm -rf pagewielder.egg-info
  rm -rf .mypy_cache
  find . -type d -name __pycache__ -exec rm -r {} +
}

action() {
  subcommand=$1
  shift

  case $subcommand in
    default)
      printf "Hello, world!\n"
      ;;
    create-env)
      create_env
      ;;
    check)
      check
      ;;
    lint)
      lint
      ;;
    clean)
      clean
      ;;
  esac

  exit 0
}

while getopts "e" name; do
  case $name in
    e)
      USE_VENV=1
      ;;
  esac
done

shift $(($OPTIND - 1))

unset name
unset OPTIND

if [ $VERBOSE -eq 1 ]; then
  printf "USE_VENV=%d\n" $USE_VENV
fi

if [ $# -eq 0 ]; then
  action default
else
  action $*
fi
