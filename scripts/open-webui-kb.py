import requests
import argparse
import os
import json


def upload_file(token, file_path, server):
    url = f'http://{server}/api/v1/files/'
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json'
    }
    files = {'file': open(file_path, 'rb')}
    response = requests.post(url, headers=headers, files=files)
    return response.json()

def add_file_to_knowledge(token, knowledge_id, file_id, server):
    url = f'http://{server}/api/v1/knowledge/{knowledge_id}/file/add'
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    data = {'file_id': file_id}
    response = requests.post(url, headers=headers, json=data)
    return response.json()

def chat_with_collection(token, model, query, collection_id, server):
    url = f'http://{server}/api/chat/completions'
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': query}],
        'files': [{'type': 'collection', 'id': collection_id}]
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

def transcribe(token, model, query, collection_id, server):
    url = f'http://{server}/api/chat/completions'
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    payload = {
        'model': model,
        # 'prompt': query
        'messages': [{'role': 'user', 'content': query}],
        "tool_ids":["transcribe"]
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

def transcription(file_name, model, whisper_server):
    url = f'http://{whisper_server}/v1/audio/transcriptions'
    files = {"file": (file_name, open(file_name, "rb"), 'audio/mp4')}
    data = {
        "model": model,
    }
    print(data)
    response = requests.post(
        url,
        files=files,
        data=data
    )
    response.raise_for_status()
    result = response.json()
    return result

parser = argparse.ArgumentParser(description='Your script description')
parser.add_argument('-s', '--server', required=True, help='Open-WebUI server')
parser.add_argument('-w', '--whisper', required=True, help='Whsiper server')
parser.add_argument('-f', '--filename', required=False, help='Name of the file')
parser.add_argument('-k', '--kbID', required=False, help='Knowledgebase UID')
parser.add_argument('-t', '--token', required=True, help='Open-WebUI token')
parser.add_argument('-q', '--query', required=False, help='KB Question')
parser.add_argument('-m', '--model', required=False, help='LLM model')
parser.add_argument('-c', '--command', required=True, help='Command to perform: upload, query, transcribe, kb')

args = parser.parse_args()
file_results = ""

# upload file if exists
if args.command == "upload":
    if not os.path.exists(args.filename):
        print(f"Error: File '{args.filename}' not found.")
    else:
        file_results = upload_file(args.token, args.filename, args.server)
        print(file_results)

if args.command == "kb":
    if not os.path.exists(args.filename):
        print(f"Error: File '{args.filename}' not found.")
    else:
        file_results = upload_file(args.token, args.filename, args.server)
        print(file_results['id'])
    if file_results['id']:
        kb_results = add_file_to_knowledge(args.token, args.kbID, file_results['id'], args.server)
        print(kb_results)
    else:
        print('File ID not found to add to KB. skipping adding to kb.')

#iterate through a list of questions array
if args.command == "query":
    if args.kbID:
        query_results = chat_with_collection(args.token, args.model, args.query, args.kbID, args.server)
        print(query_results)

if args.command == "transcribe":
    file_name = args.filename
    whisper = args.whisper
    model_size = "Systran/faster-distil-whisper-large-v3"
    transcribe_results = transcription(file_name, model_size, whisper)
    print(transcribe_results)
    file_name, file_extension = os.path.splitext(file_name)
    file_path_w = f"{file_name}.txt"
    try:
        with open(file_path_w, "w") as file:
            file.write(transcribe_results['text'])
    except Exception as e:
        print(f"Error: {e}")
    print(f"done")
