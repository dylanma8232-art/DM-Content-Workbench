"""Local Apple Vision OCR; used in a bounded subprocess, no Swift compiler required."""
import sys


def recognize(path):
    import Vision
    from Foundation import NSURL
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(['zh-Hans', 'en'])
    request.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(path), {})
    success, error = handler.performRequests_error_([request], None)
    if not success:
        raise RuntimeError('Local image recognition failed')
    rows = sorted(request.results() or [], key=lambda r: (-round(r.boundingBox().origin.y, 2), r.boundingBox().origin.x))
    return '\n'.join(r.topCandidates_(1)[0].string() for r in rows if r.topCandidates_(1))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(2)
    try:
        print(recognize(sys.argv[1]))
    except Exception:
        print('Local image recognition failed', file=sys.stderr)
        sys.exit(1)
