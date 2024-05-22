from flask import Flask, request, jsonify
from asgiref.sync import async_to_sync
from flask_cors import CORS
import asyncio
import aiohttp
from openai import AsyncOpenAI
import json
from copy import deepcopy
import os

app = Flask(__name__)
CORS(app)  # Включить CORS для всех маршрутов

async def extract_and_load_json(json_data):
    # Очистка строки от markdown-форматирования, если оно есть
    if json_data.startswith('```json'):
        # Ищем первую открывающую фигурную скобку после форматирования
        json_start = json_data.find('{')
        # Ищем последнюю закрывающую фигурную скобку перед форматированием
        json_end = json_data.rfind('}')
        # Извлекаем подстроку JSON и пытаемся её распарсить
        json_string = json_data[json_start:json_end + 1]
    else:
        # Если markdown-форматирования нет, просто пытаемся распарсить строку
        json_string = json_data

    try:
        return json.loads(json_string)
    except json.JSONDecodeError as e:
        print(e)
        return None

"""async def update_template(template, ad_text_headline, ad_text_description, image_url, new_width, new_height):
    template_copy = deepcopy(template)  # Создаем глубокую копию
    template_copy['width'] = new_width
    template_copy['height'] = new_height
    for page in template_copy['pages']:
        page['children'] = list(map(lambda child:
                         {
                             **child,
                             'text': ad_text_headline if child['id'] == 'headline' else ad_text_description if child['id'] == 'description' else child.get('text'),
                             'src': image_url if child['id'] == 'background' and 'src' in child else child.get('src')
                         },
                         page['children']))
    return template_copy"""
async def update_template(template, ad_text_headline, ad_text_description, image_url, new_width, new_height):
    template_copy = deepcopy(template)
    template_copy['width'] = new_width
    template_copy['height'] = new_height

    for page in template_copy['pages']:
        for child in page['children']:
            if child['type'] == 'image' and child['name'] == '{background_img}' and 'src' in child:
                child['src'] = image_url
            elif child['type'] == 'text' and child['text'] == '{text_headline}':
                child['text'] = ad_text_headline
            elif child['type'] == 'text' and child['text'] == '{text_description}':
                child['text'] = ad_text_description

    return template_copy
async def generate_prompts(company_name, campaign_description):
    """Генерирует заголовок и текст рекламы, а также описание для изображения."""
    client = AsyncOpenAI(api_key="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")

    async with client:
        ad_prompt = ("""USE ONLY RUSSIAN LANGUAGE. Generate a catchy ad headline and text for an advertisement. For company:"""+ company_name +""" and campaign"""+ campaign_description +""". The headline should have 5 words, and the description should have 10 words."""
                     """"You should return only the result in JSON format without other text. For example:"""
                     """{'headline': 'advertising headline','description': 'advertising description'}""")

        ad_text = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": ad_prompt}]
        )

        # PROMPT FOR MIDJOURNEY
        md_prompt = """You specialize in creating prompts for Midjourney. You start with a simple input, then generate a prompt following these rules:
Create prompts using the soft template guideline:
[Art Form and Medium] + [Subject and Scene Description] + [Specific Location] + [Visual Style and Influences] + [Technical and Artistic Details]

Where:

[Art Form and Medium]: Specifies the type of artwork and medium used.
[Subject and Scene Description]: Describes the main subjects and setting.
[Specific Location (if any)]: Includes a famous location as part of the scene.
[Visual Style and Influences]: Highlights artistic influences or specific visual styles.
[Technical and Artistic Details]: Detailed attributes about the artistic execution.
The final prompt should be written as a sentence with commas. The entire prompt should be no more than 60 words."""

        image_description = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": md_prompt},
                      {"role":"user", "content": "Creating prompts for Midjourney. For company:"+company_name+" and campaign "+campaign_description}]
        )

    return ad_text.choices[0].message.content, image_description.choices[0].message.content

async def generate_image(prompt, aspect_ratio):
    """Генерирует изображение с помощью Midjourney API (последовательно)."""
    headers = {"X-API-KEY": "75fae1f07d05a75f27a05cfceb020f0f255ca278ad1337b2f2671271c4676280"}
    async with aiohttp.ClientSession() as session:
        # Шаг 1: Запрос на генерацию изображения
        async with session.post(
            "https://api.midjourneyapi.xyz/mj/v2/imagine",
            headers=headers,
            json={"prompt": prompt, "aspect_ratio": aspect_ratio, "process_mode": "fast"}
        ) as response:
            result = await response.json()
            if result.get("status") == "failed":
                return result

        # Шаг 2: Проверка статуса генерации изображения
        task_id = result.get("task_id")
        while True:
            async with session.post("https://api.midjourneyapi.xyz/mj/v2/fetch", json={"task_id": task_id}) as check_response:
                check_result = await check_response.json()
                if check_result.get("status") in ["finished", "failed"]:
                    break
            await asyncio.sleep(5)

        if check_result.get("status") == "failed":
            return check_result

        # Шаг 3: Увеличение первого изображения
        async with session.post(
            "https://api.midjourneyapi.xyz/mj/v2/upscale",
            headers=headers,
            json={"origin_task_id": task_id, "index": "1"}
        ) as upscale_response:
            upscale_result = await upscale_response.json()
            if upscale_result.get("status") == "failed":
                return upscale_result

        # Шаг 4: Проверка статуса увеличенного изображения
        upscale_task_id = upscale_result.get("task_id")
        while True:
            async with session.post("https://api.midjourneyapi.xyz/mj/v2/fetch", json={"task_id": upscale_task_id}) as final_check_response:
                final_check_result = await final_check_response.json()
                if final_check_result.get("status") in ["finished", "failed"]:
                    break
            await asyncio.sleep(5)

        if final_check_result.get("status") == "finished":
            image_url = final_check_result.get("task_result", {}).get("image_url")
            return {"status": "success", "image_url": image_url}
        else:
            return {"status": "failed", "message": "Unable to complete the image processing."}

@app.route('/api/generate-ad', methods=['POST'])
def generate_ad():
    return async_to_sync(generate_ad_async)()  # обертываем асинхронную функцию

async def generate_ad_async():
    data = request.get_json()
    company_name = data.get('companyName')
    campaign_description = data.get('campaignDescription')
    aspect_ratio = data.get('aspect_ratio')
    width = data.get('width')
    height = data.get('height')

    print(data)

    #ЗАГОЛОВОК И ПРОМПТ ДЛЯ ИЗОБРАЖЕНИЯ
    ad_text, image_description = await generate_prompts(company_name,
                                                        campaign_description)  # Передайте данные в функцию генерации


    ad_text_json = await extract_and_load_json(ad_text)

    ad_text_headline = ad_text_json['headline']
    ad_text_description =ad_text_json['description']

    print("Generated Ad Text:", ad_text_headline)
    print("Description AD:", ad_text_description)
    print("Image Description:", image_description)

    # ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЯ
    image_result = await generate_image(image_description, aspect_ratio)  # Сгенерируйте изображение
    image_url = image_result.get("image_url")
    print("Image URL:", image_url)

    # Получаем текущий каталог, где запущен скрипт
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Создаем полный путь к JSON-файлу
    json_file_path = os.path.join(current_dir, 'data', 'polotno.json')

    # Загрузите JSON-шаблон
    with open(json_file_path, 'r') as f:
        template = json.load(f)

    # Обновляем JSON-шаблон
    updated_template = await update_template(template, ad_text_headline, ad_text_description, image_url, width, height)


    print(json.dumps(updated_template, indent=4))
    return jsonify(updated_template)

if __name__ == "__main__":
    app.run(debug=True)