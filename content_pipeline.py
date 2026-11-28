"""Keep extracted evidence separate from model interpretation, with explicit missing stages."""
import time

SPEECH_MISSING = '未取得完整讲话内容，请检查字幕或语音转写服务'
MEDIA_REASONS = {
    'ocr_unavailable': '图片文字识别未完成，请检查 OCR 工具和语言包',
    'image_unavailable': '部分配图未能下载', 'image_not_saved': '部分原图尚未保存，可继续解析重试',
    'image_understanding_unavailable': '配图画面理解未完成，请检查模型是否支持图片',
    'vision_not_configured': '未配置画面理解模型', 'vision_failed': '画面模型调用失败',
    'speech_unavailable': SPEECH_MISSING,
    'video_frames_unavailable': '视频关键画面未取得，请检查媒体访问及 ffmpeg 工具',
    'video_frames_not_saved': '部分关键帧原图尚未保存，可继续解析重试',
    'invalid_image_urls': '图片地址格式无效',
}


def media_report(item):
    kind = item.get('media_kind', 'unknown')
    parts = {'正文': bool((item.get('description_text') or item.get('extracted_body') or item.get('body') or '').strip())}
    if kind == 'image' or item.get('image_urls') or item.get('image_texts'):
        parts['图片文字'] = any(row.get('text') for row in item.get('image_texts', []))
    speech = (item.get('transcript_text') or '').strip()
    if not speech and item.get('content_level') in ('subtitle', 'transcript') and 'speech_unavailable' not in item.get('missing', []):
        speech = (item.get('extracted_body') or item.get('body') or '').strip()
    if kind != 'text' and (kind in ('video', 'audio') or speech or 'speech_unavailable' in item.get('missing', [])):
        parts['字幕或转写'] = bool(speech)
    if kind == 'video' or item.get('frame_assets') or item.get('visual_frames'):
        parts['关键画面'] = bool(item.get('frame_assets') or item.get('visual_frames'))
    reasons = [*item.get('processing_missing', []), *item.get('missing', [])]
    for row in item.get('image_texts', []):
        if row.get('error'):
            reasons.extend(row['error'].split(','))
        elif row.get('status') in ('missing', 'failed', 'unavailable'):
            reasons.append(f"图片 {row.get('index', '')} 未取得识别文字，原记录未保存具体错误")
    missing = list(dict.fromkeys(MEDIA_REASONS.get(reason, reason) for reason in reasons if reason))
    if not missing and item.get('processing_status') == 'partial':
        missing.append(item.get('analysis_error') or '此记录未保存具体失败原因')
    return {'parts': parts, 'speech': speech if '字幕或转写' in parts else '', 'missing': missing, 'status': item.get('processing_status'),
            'can_retry': parts.get('字幕或转写') is False and bool(item.get('url'))}

def process(platform, rows, topic='', media_cache=None):
    import content_media
    import content_understanding

    results = []
    deadline = time.monotonic() + 120
    for source in rows:
        item = dict(source)
        if time.monotonic() >= deadline:
            item.update(processing_status='partial', processing_missing=['本轮处理时间已到，尚未完成图文或视频解析'],
                        analysis_status='unavailable')
            results.append(item)
            continue
        try:
            item = content_media.enrich_media(platform, item, deadline, cache=media_cache) if media_cache else content_media.enrich_media(platform, item, deadline)
        except Exception:
            item['media_status'] = 'partial'
            item['missing'] = ['媒体提取未完成，可重试']
        original = item.get('transcript_text') or item.get('extracted_body') or item.get('body', '')
        item['extracted_body'] = original
        # OCR is extracted text, never a model-generated paraphrase of the original.
        sections = [original] if original else []
        for image in item.get('image_texts', []):
            if image.get('text') and image.get('status') in ('ready', 'ok', 'completed', 'success'):
                sections.append(f"[图片 {image['index']} 识别文字]\n{image['text']}")
        item['body'] = '\n\n'.join(sections)[:50000]
        try:
            interpreted = content_understanding.analyze(item, topic=topic)
            # The analyzer returns analysis metadata, never replaces the source body.
            for key in ('analysis_status', 'analysis', 'analysis_error', 'analysis_model',
                        'analysis_version', 'analysis_fingerprint', 'analysis_truncated', 'evidence'):
                if key in interpreted:
                    item[key] = interpreted[key]
        except Exception:
            item.update(analysis_status='failed', analysis_error='内容解析失败，已保留提取结果')
        missing = [MEDIA_REASONS.get(reason, reason) for reason in item.get('missing', [])]
        if item.get('analysis', {}).get('missing'):
            missing.extend(item['analysis']['missing'])
        if item.get('media_parts', 1) > 1:
            missing.append('多分集作品目前仅处理第 1 集')
        if item.get('media_kind', 'unknown') == 'unknown':
            missing.append('尚未确认作品是否包含配图或视频')
        if item.get('content_level') in ('title_only', 'summary', 'visible_text'):
            missing.append('作品详情未完整提取')
        if item.get('images_incomplete'):
            missing.append('配图尚未全部取得，或已达到单作品 24 张的处理上限')
        if item.get('analysis_truncated'):
            missing.append('内容较长，本轮解析仅覆盖部分证据；完整提取结果仍保留')
        if item.get('content_truncated'):
            missing.append('原文达到保存上限')
        if item.get('analysis_status') != 'ready':
            missing.append(item.get('analysis_error') or '内容理解未完成，请检查模型配置')
        item['processing_missing'] = list(dict.fromkeys(missing))
        item['processing_status'] = 'complete' if not missing else 'partial'
        results.append(item)
    return results
