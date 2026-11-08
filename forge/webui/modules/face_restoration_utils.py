from __future__ import annotations

import logging
import os
from functools import cached_property
from typing import TYPE_CHECKING, Callable

import cv2
import numpy as np
import torch

from modules import devices, errors, face_restoration, shared
from modules_forge.forge_util import prepare_free_memory

if TYPE_CHECKING:
    from facexlib.utils.face_restoration_helper import FaceRestoreHelper

logger = logging.getLogger(__name__)


def bgr_image_to_rgb_tensor(img: np.ndarray) -> torch.Tensor:
    """Convert a BGR NumPy image in [0..1] range to a PyTorch RGB float32 tensor."""
    assert img.shape[2] == 3, "image must be RGB"
    if img.dtype == "float64":
        img = img.astype("float32")
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return torch.from_numpy(img.transpose(2, 0, 1)).float()


def rgb_tensor_to_bgr_image(tensor: torch.Tensor, *, min_max=(0.0, 1.0)) -> np.ndarray:
    """
    Convert a PyTorch RGB tensor in range `min_max` to a BGR NumPy image in [0..1] range.
    """
    tensor = tensor.squeeze(0).float().detach().cpu().clamp_(*min_max)
    tensor = (tensor - min_max[0]) / (min_max[1] - min_max[0])
    assert tensor.dim() == 3, "tensor must be RGB"
    img_np = tensor.numpy().transpose(1, 2, 0)
    if img_np.shape[2] == 1:  # gray image, no RGB/BGR required
        return np.squeeze(img_np, axis=2)
    return cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB)


def create_face_helper(device) -> FaceRestoreHelper:
    from facexlib.detection import retinaface
    from facexlib.utils.face_restoration_helper import FaceRestoreHelper
    if hasattr(retinaface, 'device'):
        retinaface.device = device
    return FaceRestoreHelper(
        upscale_factor=1,
        face_size=512,
        crop_ratio=(1, 1),
        det_model='retinaface_resnet50',
        save_ext='png',
        use_parse=True,
        device=device,
    )


# While a video runs: the face_restoration_video.FaceSteadier that keeps its restored faces from shimmering
# between frames. None for pictures.
steadier = None

# what the face parsing model calls skin, eyes, nose, mouth and the rest of a face (255), and what it calls
# background, hair, hat and neck (0); from facexlib's paste_faces_to_input_image()
FACE_PARTS = [0, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 0, 255, 0, 0, 0]


def paste_faces(face_helper: FaceRestoreHelper) -> np.ndarray:
    """
    The restored faces put back into the picture: facexlib's paste_faces_to_input_image(), done on the part of
    the picture each face covers instead of on all of it.

    facexlib warps every face and its mask to the size of the whole picture, and blends the whole picture in
    float64 once per face. Outside the face all of that is zeros and changes nothing, but for a 2160x3840 frame
    with two faces it takes 1.4 s, most of a video frame's time; here it takes 0.3 s at any frame size. The steps
    and their numbers are facexlib's, so the picture is the same one. Measured on 720p to 4K frames: under 1 in
    100 of the restored pixels differ at all, by 1 of 255 and rarely 2, because OpenCV rounds a warp's positions
    a little differently when it starts from another corner.
    """
    from torchvision.transforms.functional import normalize

    image = face_helper.input_img
    if not face_helper.use_parse or face_helper.upscale_factor != 1 or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        return face_helper.paste_faces_to_input_image()      # cases Forge does not use: left to facexlib

    image = image.copy()
    height, width = image.shape[:2]
    for restored_face, inverse_affine in zip(face_helper.restored_faces, face_helper.inverse_affine_matrices):
        # which parts of the restored square are face: only those replace the picture, with a soft edge
        face_input = cv2.resize(restored_face, (512, 512), interpolation=cv2.INTER_LINEAR)
        face_input = bgr_image_to_rgb_tensor(face_input.astype('float32') / 255.)
        normalize(face_input, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
        with torch.no_grad():
            parts = face_helper.face_parse(face_input.unsqueeze(0).to(face_helper.device))[0]
        parts = parts.argmax(dim=1).squeeze().cpu().numpy()
        mask = np.zeros(parts.shape)
        for index, value in enumerate(FACE_PARTS):
            mask[parts == index] = value
        mask = cv2.GaussianBlur(mask, (101, 101), 11)
        mask = cv2.GaussianBlur(mask, (101, 101), 11)
        mask[:10, :] = mask[-10:, :] = 0
        mask[:, :10] = mask[:, -10:] = 0
        mask = cv2.resize(mask / 255., restored_face.shape[:2])

        # where the square lands in the picture
        side_y, side_x = restored_face.shape[:2]
        corners = inverse_affine @ np.array([[0, side_x, side_x, 0], [0, 0, side_y, side_y], [1, 1, 1, 1]], dtype=np.float64)
        left, top = max(0, int(np.floor(corners[0].min())) - 2), max(0, int(np.floor(corners[1].min())) - 2)
        right, bottom = min(width, int(np.ceil(corners[0].max())) + 2), min(height, int(np.ceil(corners[1].max())) + 2)
        if right <= left or bottom <= top:
            continue
        to_part = inverse_affine.copy()
        to_part[:, 2] -= (left, top)
        size = (right - left, bottom - top)
        face = cv2.warpAffine(restored_face, to_part, size)
        weight = cv2.warpAffine(mask, to_part, size, flags=3)[:, :, None]
        part = image[top:bottom, left:right]
        image[top:bottom, left:right] = (weight * face + (1 - weight) * part).astype(np.uint8)
    return image


def restore_with_face_helper(
    np_image: np.ndarray,
    face_helper: FaceRestoreHelper,
    restore_face: Callable[[torch.Tensor], torch.Tensor],
) -> np.ndarray:
    """
    Find faces in the image using face_helper, restore them using restore_face, and paste them back into the image.

    `restore_face` should take a cropped face image and return a restored face image.
    """
    from torchvision.transforms.functional import normalize
    # laid out in memory as BGR, not only read that way: OpenCV and PIL copy a reversed view on every use, and
    # PIL takes 120 ms to read a 4K frame from one
    np_image = np.ascontiguousarray(np_image[:, :, ::-1])
    original_resolution = np_image.shape[0:2]

    try:
        logger.debug("Detecting faces...")
        face_helper.clean_all()
        face_helper.read_image(np_image)
        if steadier is not None:
            steadier.find_faces(face_helper, resize=640, eye_dist_threshold=5)
        else:
            face_helper.get_face_landmarks_5(only_center_face=False, resize=640, eye_dist_threshold=5)
            face_helper.align_warp_face()
        logger.debug("Found %d faces, restoring", len(face_helper.cropped_faces))
        for index, cropped_face in enumerate(face_helper.cropped_faces):
            if steadier is not None and steadier.is_lost(face_helper, index):
                face_helper.add_restored_face(steadier.carry(face_helper, index, cropped_face))
                continue

            cropped_face_t = bgr_image_to_rgb_tensor(cropped_face / 255.0)
            normalize(cropped_face_t, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
            cropped_face_t = cropped_face_t.unsqueeze(0).to(devices.device_codeformer)

            try:
                with torch.no_grad():
                    cropped_face_t = restore_face(cropped_face_t)
                devices.torch_gc()
            except Exception:
                errors.report('Failed face-restoration inference', exc_info=True)

            restored_face = rgb_tensor_to_bgr_image(cropped_face_t, min_max=(-1, 1))
            restored_face = (restored_face * 255.0).astype('uint8')
            if steadier is not None:
                restored_face = steadier.steady(face_helper, index, cropped_face, restored_face)
            face_helper.add_restored_face(restored_face)

        logger.debug("Merging restored faces into image")
        face_helper.get_inverse_affine(None)
        img = paste_faces(face_helper)
        img = np.ascontiguousarray(img[:, :, ::-1])
        if original_resolution != img.shape[0:2]:
            img = cv2.resize(
                img,
                (0, 0),
                fx=original_resolution[1] / img.shape[1],
                fy=original_resolution[0] / img.shape[0],
                interpolation=cv2.INTER_LINEAR,
            )
        logger.debug("Face restoration complete")
    finally:
        face_helper.clean_all()
    return img


class CommonFaceRestoration(face_restoration.FaceRestoration):
    net: torch.Module | None
    model_url: str
    model_download_name: str

    def __init__(self, model_path: str):
        super().__init__()
        self.net = None
        self.model_path = model_path
        os.makedirs(model_path, exist_ok=True)

    @cached_property
    def face_helper(self) -> FaceRestoreHelper:
        return create_face_helper(self.get_device())

    def send_model_to(self, device):
        if self.net:
            logger.debug("Sending %s to %s", self.net, device)
            self.net.to(device)
        if self.face_helper:
            logger.debug("Sending face helper to %s", device)
            self.face_helper.face_det.to(device)
            self.face_helper.face_parse.to(device)

    def get_device(self):
        raise NotImplementedError("get_device must be implemented by subclasses")

    def load_net(self) -> torch.Module:
        raise NotImplementedError("load_net must be implemented by subclasses")

    def restore_with_helper(
        self,
        np_image: np.ndarray,
        restore_face: Callable[[torch.Tensor], torch.Tensor],
    ) -> np.ndarray:
        try:
            if self.net is None:
                self.net = self.load_net()
        except Exception:
            logger.warning("Unable to load face-restoration model", exc_info=True)
            return np_image

        try:
            prepare_free_memory()
            self.send_model_to(self.get_device())
            return restore_with_face_helper(np_image, self.face_helper, restore_face)
        finally:
            if shared.opts.face_restoration_unload:
                self.send_model_to(devices.cpu)


def patch_facexlib(dirname: str) -> None:
    import facexlib.detection
    import facexlib.parsing

    det_facex_load_file_from_url = facexlib.detection.load_file_from_url
    par_facex_load_file_from_url = facexlib.parsing.load_file_from_url

    def update_kwargs(kwargs):
        return dict(kwargs, save_dir=dirname, model_dir=None)

    def facex_load_file_from_url(**kwargs):
        return det_facex_load_file_from_url(**update_kwargs(kwargs))

    def facex_load_file_from_url2(**kwargs):
        return par_facex_load_file_from_url(**update_kwargs(kwargs))

    facexlib.detection.load_file_from_url = facex_load_file_from_url
    facexlib.parsing.load_file_from_url = facex_load_file_from_url2
