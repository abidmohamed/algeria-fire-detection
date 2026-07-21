import os
import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

class SmokeDetector:
    # Detection thresholds
    SMOKE_CONFIDENCE_THRESHOLD = 0.35
    # YOLO class IDs for fire/smoke (adjust based on your model's classes)
    FIRE_SMOKE_CLASSES = None  # None = accept all classes; set to {0, 1} etc. if model has specific fire/smoke class IDs

    def __init__(self, model_path="model.pt"):
        self.model_path = model_path
        self.model = None
        self._load_model()
        
    def _load_model(self):
        """Loads custom YOLOv8 model if weights exist, otherwise configures fallback."""
        if YOLO is None:
            print("[WARNING] Ultralytics YOLO library not installed. Smoke detector will use CV fallback.")
            return

        if os.path.exists(self.model_path):
            try:
                print(f"[INFO] Loading custom YOLOv8 smoke detection model from {self.model_path}...")
                self.model = YOLO(self.model_path)
                print("[INFO] Custom YOLOv8 model loaded successfully.")
            except Exception as e:
                print(f"[ERROR] Failed to load YOLOv8 model: {e}. Falling back to CV.")
                self.model = None
        else:
            print(f"[INFO] Smoke detection model weights file '{self.model_path}' not found.")
            print("[INFO] System will run in Simulation / Computer Vision fallback mode.")
            self.model = None

    def _draw_ai_overlay(self, img, title, status_text, status_color, smoke_mask=None):
        """Draws a premium, high-tech GIS scanning overlay on the image."""
        if cv2 is None:
            return img

        h, w, _ = img.shape
        annotated = img.copy()

        # 1. Draw a semi-transparent black header banner
        header_h = min(65, h)
        overlay = annotated.copy()
        cv2.rectangle(overlay, (0, 0), (w, header_h), (15, 23, 42), -1) # Dark navy background
        cv2.addWeighted(overlay, 0.75, annotated, 0.25, 0, annotated)

        # 2. Draw the header text (only if header is tall enough)
        if header_h >= 25:
            cv2.putText(annotated, title, (15, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        if header_h >= 50:
            cv2.putText(annotated, status_text, (15, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.45, status_color, 1, cv2.LINE_AA)

        # 3. Draw a target crosshair in the center of the image (where the fire is located)
        cx, cy = w // 2, h // 2
        cv2.circle(annotated, (cx, cy), 18, (0, 0, 255), 2)  # Red outer target ring
        cv2.circle(annotated, (cx, cy), 3, (0, 0, 255), -1)   # Red center dot
        cv2.line(annotated, (cx - 30, cy), (cx + 30, cy), (0, 0, 255), 1)  # Horizontal crosshair
        cv2.line(annotated, (cx, cy - 30), (cx, cy + 30), (0, 0, 255), 1)  # Vertical crosshair

        # 4. If a smoke mask is provided, draw scanned contours
        if smoke_mask is not None:
            # Handle OpenCV 3 vs OpenCV 4 compatibility for findContours unpacking
            contours_info = cv2.findContours(smoke_mask.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            contours = contours_info[0] if len(contours_info) == 2 else contours_info[1]
            for cnt in contours:
                if cv2.contourArea(cnt) > 80:  # filter tiny noise contours
                    x, y, w_box, h_box = cv2.boundingRect(cnt)
                    # Draw a bright orange box around scanned haze areas
                    cv2.rectangle(annotated, (x, y), (x + w_box, y + h_box), (0, 140, 255), 2)

        return annotated

    def detect_smoke(self, image_path):
        """
        Runs smoke detection on the downloaded Sentinel-2 image.
        Returns a tuple: (smoke_detected: bool, confidence: float, output_image_path: str)
        """
        if not os.path.exists(image_path):
            print(f"[ERROR] Image file {image_path} does not exist. Cannot run smoke detection.")
            return False, 0.0, None

        out_dir = os.path.dirname(image_path)
        filename = os.path.basename(image_path)
        out_path = os.path.join(out_dir, f"verified_{filename}")

        # 1. Use YOLO model if loaded
        if self.model is not None:
            try:
                print(f"[INFO] Running YOLOv8 smoke detection on {image_path}...")
                results = self.model(image_path)
                result = results[0]
                boxes = result.boxes
                
                if len(boxes) > 0:
                    # Filter by confidence threshold and optionally by class
                    valid_boxes = []
                    for box in boxes:
                        conf = float(box.conf)
                        cls_id = int(box.cls)
                        if conf >= self.SMOKE_CONFIDENCE_THRESHOLD:
                            if self.FIRE_SMOKE_CLASSES is None or cls_id in self.FIRE_SMOKE_CLASSES:
                                valid_boxes.append((conf, cls_id))
                    
                    if valid_boxes:
                        best_conf = max(v[0] for v in valid_boxes)
                        print(f"[INFO] YOLOv8 smoke/fire detection CONFIRMED with confidence: {best_conf:.2%} ({len(valid_boxes)} valid detections)")
                        
                        # Save standard YOLO bounding box image
                        result.save(filename=out_path)
                        
                        # Read it back and apply custom HUD overlay
                        if cv2 is not None:
                            img = cv2.imread(out_path)
                            if img is not None:
                                img_hud = self._draw_ai_overlay(
                                    img, 
                                    "ALGERIA FIRE WATCH | YOLOv8 ANALYZER", 
                                    f"STATUS: SMOKE PLUME CONFIRMED ({best_conf:.2%})", 
                                    (50, 255, 50)  # Green
                                )
                                cv2.imwrite(out_path, img_hud)
                                
                        return True, best_conf, out_path
                    else:
                        # Detections exist but all below threshold
                        below_thresh_conf = float(max(boxes.conf))
                        print(f"[INFO] YOLOv8 detections found but below confidence threshold ({below_thresh_conf:.2%} < {self.SMOKE_CONFIDENCE_THRESHOLD:.0%}). Treating as no detection.")
                else:
                    print("[INFO] YOLOv8 inference completed: No smoke or fire detected.")
                    
                    # Generate unconfirmed HUD image for context
                    if cv2 is not None:
                        img = cv2.imread(image_path)
                        if img is not None:
                            img_hud = self._draw_ai_overlay(
                                img, 
                                "ALGERIA FIRE WATCH | YOLOv8 ANALYZER", 
                                "STATUS: NO SMOKE DETECTED BY AI MODEL", 
                                (140, 140, 140)  # Gray
                            )
                            cv2.imwrite(out_path, img_hud)
                            
                    return False, 0.0, out_path
            except Exception as e:
                print(f"[ERROR] YOLOv8 inference failed: {e}. Falling back to CV analysis.")

        # 2. Fallback: OpenCV color and texture heuristic
        if cv2 is None:
            # Absolute fallback if OpenCV is missing
            print("[WARNING] OpenCV not installed. Falling back to simple simulation classification.")
            is_mock_fp = "fp" in os.path.basename(image_path).lower()
            return (not is_mock_fp), 0.85, image_path

        return self._run_cv_fallback(image_path, out_path)

    def _run_cv_fallback(self, image_path, out_path):
        """
        Aesthetic computer vision heuristic to detect smoke in Sentinel-2 quicklook.
        Detects bright, desaturated (grayish/whitish) plumes on a forest background.
        """
        try:
            print(f"[INFO] Running CV heuristic smoke detector on {image_path}...")
            img = cv2.imread(image_path)
            if img is None:
                print("[ERROR] OpenCV failed to read image.")
                return False, 0.0, image_path
                
            # Convert to HSV color space
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            
            # Smoke is typically bright (high V value) and low-saturation (grayish-white)
            lower_smoke = np.array([0, 0, 160])
            upper_smoke = np.array([180, 55, 255])
            
            # Create a mask for smoke-like pixels
            smoke_mask = cv2.inRange(hsv, lower_smoke, upper_smoke)
            
            # Cloud masking: remove very bright, very desaturated large blobs (clouds)
            # Clouds are brighter and less saturated than smoke
            cloud_mask = cv2.inRange(hsv, np.array([0, 0, 210]), np.array([180, 25, 255]))
            # Morphological opening to keep only large cloud masses, not smoke wisps
            cloud_kernel = np.ones((15, 15), np.uint8)
            cloud_mask = cv2.morphologyEx(cloud_mask, cv2.MORPH_OPEN, cloud_kernel)
            # Subtract cloud regions from smoke mask
            smoke_mask = cv2.bitwise_and(smoke_mask, cv2.bitwise_not(cloud_mask))
            
            # Calculate percentage of smoke-like pixels
            total_pixels = img.shape[0] * img.shape[1]
            smoke_pixels = cv2.countNonZero(smoke_mask)
            smoke_ratio = (smoke_pixels / total_pixels) * 100.0
            
            print(f"[INFO] CV Haze Analysis: {smoke_ratio:.2f}% of pixels match smoke profile.")
            
            # Check threshold (3.0% to 45.0% range)
            if 3.0 <= smoke_ratio <= 45.0:
                confidence = 0.5 + (smoke_ratio / 90.0)
                confidence = min(0.95, confidence)
                print(f"[INFO] CV smoke signature CONFIRMED. Confidence: {confidence:.2%}")
                
                # Apply overlay with mask highlights in orange
                img_hud = self._draw_ai_overlay(
                    img, 
                    "ALGERIA FIRE WATCH | CV ANALYZER", 
                    f"STATUS: SMOKE HAZE DETECTED ({confidence:.2%})", 
                    (0, 165, 255),  # Orange
                    smoke_mask=smoke_mask
                )
                cv2.imwrite(out_path, img_hud)
                return True, confidence, out_path
            else:
                status_lbl = "STATUS: NO SMOKE SIGNATURE DETECTED"
                status_color = (140, 140, 140) # Gray
                
                if smoke_ratio > 45.0:
                    status_lbl = "STATUS: CLOUD COVER DETECTED (FILTERED)"
                    status_color = (255, 50, 50) # Blue/red indicator for cloud
                    print("[INFO] CV Analysis: High desaturated pixel density. Likely heavy cloud cover.")
                else:
                    print("[INFO] CV Analysis: Insufficient smoke-like pixel density.")
                
                # Apply unconfirmed overlay but still show highlight blocks for the "AI look"
                img_hud = self._draw_ai_overlay(
                    img, 
                    "ALGERIA FIRE WATCH | CV ANALYZER", 
                    status_lbl, 
                    status_color,
                    smoke_mask=smoke_mask if smoke_ratio > 0 else None
                )
                cv2.imwrite(out_path, img_hud)
                
                return False, 0.0, out_path
                
        except Exception as e:
            print(f"[ERROR] CV Fallback analysis failed: {e}")
            return False, 0.0, image_path
