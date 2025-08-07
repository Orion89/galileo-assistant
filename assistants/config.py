import os, json
from openai import OpenAI

def ensure_assistant(assistant_id:str=None, assistant_def:dict=None):
    import openai, os
    oai = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    if assistant_id:
        # update/check drift
        remote = oai.beta.assistants.retrieve(assistant_id).model_dump()
        patch = {k:v for k,v in assistant_def.items() if remote.get(k)!=v}
        if patch:
            oai.beta.assistants.update(assistant_id, **patch)
        return assistant_id
    else:
        new_a = oai.beta.assistants.create(**assistant_def)
        print(f"Created new assistant: {new_a.id}")
        return new_a.id

oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASST_ID = "asst_h9k83Vkidxq3sBfqjc7FhfZD" # os.getenv("ASSISTANT_ID")

assistant_def = oai.beta.assistants.retrieve(ASST_ID)
# print(json.dumps(assistant_def.model_dump(), indent=2))
if not os.path.exists(os.path.join(os.path.dirname(__file__), "config_galileo_v1.1.0.json")):
    with open(os.path.join(os.path.dirname(__file__), "config_galileo_v1.1.0.json"), "w") as f:
        json.dump(assistant_def.model_dump(), f, indent=2)
