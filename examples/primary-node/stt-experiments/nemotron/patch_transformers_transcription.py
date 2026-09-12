"""Patch Transformers 5.13.0 serve to honor ASR language conditioning.

The upstream OpenAI transcription handler validates but intentionally ignores
``language``. Nemotron 3.5 requires that processor argument for explicit locale
conditioning and ``auto`` language tagging. Fail if the pinned source changes
instead of applying a partial patch.
"""
from pathlib import Path

import transformers.cli.serving.transcription as transcription


path = Path(transcription.__file__)
source = path.read_text(encoding="utf-8")
replacements = (
    ('    "language",\n', ""),
    (
        '            stream = str(form.get("stream", "false")).lower() == "true"\n',
        '            stream = str(form.get("stream", "false")).lower() == "true"\n'
        '            language = form.get("language")\n'
        '            if language is not None and not isinstance(language, str):\n'
        '                raise HTTPException(status_code=422, detail="Expected language as string")\n',
    ),
    (
        "        audio_inputs = self._prepare_audio_inputs(file_bytes, audio_processor, audio_model)\n",
        "        audio_inputs = self._prepare_audio_inputs(file_bytes, audio_processor, audio_model, language)\n",
    ),
    (
        "        return await self._non_streaming(gen_manager, audio_model, audio_processor, audio_inputs)\n",
        "        return await self._non_streaming(gen_manager, audio_model, audio_processor, audio_inputs, language)\n",
    ),
    (
        '        file_bytes: bytes, audio_processor: "ProcessorMixin", audio_model: "PreTrainedModel"\n',
        '        file_bytes: bytes, audio_processor: "ProcessorMixin", audio_model: "PreTrainedModel", '
        "language: str | None\n",
    ),
    (
        "        audio_inputs = audio_processor(audio_array, sampling_rate=sampling_rate, return_tensors=\"pt\").to(\n",
        "        processor_kwargs = {\"sampling_rate\": sampling_rate, \"return_tensors\": \"pt\"}\n"
        "        if language:\n"
        "            processor_kwargs[\"language\"] = language\n"
        "        audio_inputs = audio_processor(audio_array, **processor_kwargs).to(\n",
    ),
    (
        "        audio_inputs: dict,\n    ) -> JSONResponse:\n",
        "        audio_inputs: dict,\n        language: str | None,\n    ) -> JSONResponse:\n",
    ),
    (
        "        generated_ids = await gen_manager.async_submit(audio_model.generate, **audio_inputs)\n"
        "        text = audio_processor.batch_decode(generated_ids, skip_special_tokens=True)[0]\n"
        "        return JSONResponse(Transcription(text=text).model_dump(exclude_none=True))\n",
        "        generation_output = await gen_manager.async_submit(audio_model.generate, **audio_inputs)\n"
        "        generated_ids = getattr(generation_output, \"sequences\", generation_output)\n"
        "        text = audio_processor.batch_decode(generated_ids, skip_special_tokens=True)[0]\n"
        "        payload = Transcription(text=text).model_dump(exclude_none=True)\n"
        "        if language == \"auto\":\n"
        "            import re\n"
        "            tagged = audio_processor.batch_decode(generated_ids, skip_special_tokens=False)[0]\n"
        "            match = re.search(r\"<([a-z]{2}(?:-[A-Z]{2})?)>\", tagged)\n"
        "            if match:\n"
        "                payload[\"language\"] = match.group(1)\n"
        "        return JSONResponse(payload)\n",
    ),
)
for old, new in replacements:
    count = source.count(old)
    if count != 1:
        raise RuntimeError("expected one Transformers 5.13.0 patch target, found %d: %r" % (count, old))
    source = source.replace(old, new)
path.write_text(source, encoding="utf-8")
