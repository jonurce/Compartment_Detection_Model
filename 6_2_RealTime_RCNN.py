import os
import cv2
import torch
import torchvision
from torchvision.models.detection import fasterrcnn_resnet50_fpn
from torchvision.transforms import ToTensor
from torchvision.ops import nms
import numpy as np
import datetime
import time
import psutil
import csv

# Try importing NVML (GPU monitoring)
try:
    from pynvml import (
        nvmlInit, nvmlDeviceGetHandleByIndex,
        nvmlDeviceGetUtilizationRates, nvmlDeviceGetPowerUsage
    )

    nvml_available = True
except ImportError:
    nvml_available = False

# Paths
nn = 2
model_name = f"faster_rcnn_NN{nn}"
model_path = f"Datasets/Dataset_NN{nn}/Runs_NN{nn}/{model_name}/best.pth"
output_dir = f"RealTime/{model_name}"
csv_path = os.path.join(output_dir, "performance_metrics.csv")
os.makedirs(output_dir, exist_ok=True)

# Device
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
use_cuda = torch.cuda.is_available()
if use_cuda and nvml_available:
    nvmlInit()
    handle = nvmlDeviceGetHandleByIndex(0)

print(f"Using device: {device}")


# Load Faster R-CNN model
def get_model(num_classes, model_path, device):
    model = fasterrcnn_resnet50_fpn(pretrained=False)
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = torchvision.models.detection.faster_rcnn.FastRCNNPredictor(in_features, num_classes)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.to(device)
    model.eval()
    return model


if nn == 1:
    num_classes = 3  # Background, A, B
else:
    num_classes = 11
model = get_model(num_classes, model_path, device)

# Initialize camera
camera_index = 6
cap = cv2.VideoCapture(camera_index)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_FPS, 30)

if not cap.isOpened():
    print(f"Error: Could not open camera at index {camera_index}")
    exit()

width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)
print(f"Camera opened at {width}x{height} @ {fps} FPS")

# Parameters
conf = 0.7
iou = 0.5
class_names = ["background", "Drone", "Station"]
transform = ToTensor()

# Metrics storage
metrics_list = []
start_time = time.time()
duration = 10  # Run for 10 seconds

try:
    print(
        "Real-time detection started for 10 seconds. Press SPACE to save frame, 'q' to quit early, 'u'/'d' for conf, 'i'/'o' for IoU.")
    while (time.time() - start_time) < duration:
        ret, frame = cap.read()
        if not ret:
            print("Error: Failed to capture frame")
            break

        # Preprocess
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img_tensor = transform(img_rgb).to(device)

        if use_cuda:
            torch.cuda.reset_peak_memory_stats()

        # Inference timing
        start = time.perf_counter()
        with torch.no_grad():
            predictions = model([img_tensor])[0]
        end = time.perf_counter()

        inference_time_ms = (end - start) * 1000
        current_fps = 1000 / inference_time_ms

        # Extract predictions
        boxes = predictions["boxes"].cpu().numpy()
        scores = predictions["scores"].cpu().numpy()
        labels = predictions["labels"].cpu().numpy()

        # NMS + Confidence filtering
        keep = nms(torch.tensor(boxes, dtype=torch.float32), torch.tensor(scores), iou_threshold=iou)
        boxes = boxes[keep]
        scores = scores[keep]
        labels = labels[keep]
        mask = scores >= conf
        boxes, scores, labels = boxes[mask], scores[mask], labels[mask]

        # Draw predictions
        for box, score, label in zip(boxes, scores, labels):
            x1, y1, x2, y2 = map(int, box)
            class_name = class_names[label]
            label_text = f"{class_name} {score:.2f}"
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, label_text, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # === System metrics ===
        cpu_util = psutil.cpu_percent()
        gpu_util = nvmlDeviceGetUtilizationRates(handle).gpu if use_cuda and nvml_available else "N/A"
        power_w = nvmlDeviceGetPowerUsage(handle) / 1000 if use_cuda and nvml_available else "N/A"
        vram_gb = torch.cuda.max_memory_allocated() / 1e9 if use_cuda else "N/A"

        # === Store metrics ===
        metrics_list.append({
            "inference_time_ms": inference_time_ms,
            "fps": current_fps,
            "cpu_percent": cpu_util,
            "gpu_percent": gpu_util if gpu_util != "N/A" else None,
            "vram_gb": vram_gb if vram_gb != "N/A" else None,
            "power_w": power_w if power_w != "N/A" else None
        })

        # === Overlay metrics ===
        metrics = [
            f"Inference: {inference_time_ms:.1f} ms",
            f"FPS: {current_fps:.1f}",
            f"CPU: {cpu_util:.1f}%",
            f"GPU: {gpu_util}%" if gpu_util != "N/A" else "GPU: N/A",
            f"VRAM: {vram_gb:.2f} GB" if isinstance(vram_gb, float) else "VRAM: N/A",
            f"Power: {power_w:.1f} W" if isinstance(power_w, float) else "Power: N/A",
            f"Conf: {conf:.2f} IoU: {iou:.2f}"
        ]

        for i, text in enumerate(metrics):
            cv2.putText(frame, text, (10, 25 + 25 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)


        # Show frame
        cv2.imshow("L515 Faster R-CNN Detection", frame)

        # Keyboard input
        key = cv2.waitKey(1) & 0xFF
        if key == ord(' '):
            timestamp_img = datetime.datetime.now().strftime('%Y_%m_%d_%H_%M_%S')
            filename = os.path.join(output_dir, f'detection_{timestamp_img}.jpg')
            cv2.imwrite(filename, frame)
            print(f"Detection saved: {filename}")
        elif key == ord('q'):
            print("Exiting early...")
            break
        elif key == ord('u'):
            conf = min(conf + 0.1, 1.0)
        elif key == ord('d'):
            conf = max(conf - 0.1, 0.1)
        elif key == ord('i'):
            iou = min(iou + 0.1, 1.0)
        elif key == ord('o'):
            iou = max(iou - 0.1, 0.1)

    # === Compute and print averages ===
    if metrics_list:
        avg_inference_time = np.mean([m["inference_time_ms"] for m in metrics_list])
        avg_fps = np.mean([m["fps"] for m in metrics_list])
        avg_cpu_util = np.mean([m["cpu_percent"] for m in metrics_list])

        # Handle GPU-related metrics only if available
        valid_gpu_utils = [m["gpu_percent"] for m in metrics_list if m["gpu_percent"] is not None]
        avg_gpu_util = np.mean(valid_gpu_utils) if valid_gpu_utils else "N/A"

        valid_vram = [m["vram_gb"] for m in metrics_list if m["vram_gb"] is not None]
        max_vram = max(valid_vram) if valid_vram else "N/A"

        valid_power = [m["power_w"] for m in metrics_list if m["power_w"] is not None]
        avg_power = np.mean(valid_power) if valid_power else "N/A"

        print("\n=== Performance Summary ===")
        print(f"Average Inference Time: {avg_inference_time:.2f} ms")
        print(f"Average FPS: {avg_fps:.2f}")
        print(f"Average CPU Utilization: {avg_cpu_util:.2f}%")
        print(
            f"Average GPU Utilization: {avg_gpu_util:.2f}%" if avg_gpu_util != "N/A" else "Average GPU Utilization: N/A")
        print(f"Max VRAM Usage: {max_vram:.3f} GB" if max_vram != "N/A" else "Max VRAM Usage: N/A")
        print(
            f"Average Power Consumption: {avg_power:.2f} W" if avg_power != "N/A" else "Average Power Consumption: N/A")

finally:
    cap.release()
    cv2.destroyAllWindows()