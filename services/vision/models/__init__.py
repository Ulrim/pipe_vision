"""학습 산출물/ONNX 모델 배포 위치 (§6.3).

데이터 축적 후 PyTorch 학습 → ONNX export 산출물(surface_*.onnx)을 여기에 둔다.
모델 미배포 시 surface 모듈은 고전 CV 폴백으로 동작한다("동작하는 폴백 → 점진 고도화").
"""
