#!/bin/bash

# Define a list of questions
questions=("How do I activate a trial license?" "How do I limit the concurrent session limits for admins?" "How do I delete a channel?")
server=192.168.71.5
token=<ow token>
model="meta/llama-3.1-8b-instruct"
kb="d95c856a-6ade-4dfb-b599-a47f75a1b5f0"

# Python script to process questions
python_script="test.py"

# Iterate through questions
for question in "${questions[@]}"; do
  # Pass question to Python script as command line argument
  $python_script --server $server --token $token --query "$question" --model $model --kbID $kb
done