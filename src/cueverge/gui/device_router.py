from fastapi import APIRouter, HTTPException, Request

from cueverge.gui.device_ca import DeviceCertificateAuthorityError
from cueverge.gui.device_enrollment import (
    DeviceEnrollmentService,
    EnrollmentRateLimitError,
)
from cueverge.gui.repository import PairingTokenNotUsableError
from cueverge.protocol.device_v2 import (
    DeviceEnrollmentRequest,
    DeviceEnrollmentResponse,
)


def create_device_enrollment_router(
    service: DeviceEnrollmentService,
) -> APIRouter:
    router = APIRouter(prefix="/api/device/v2", tags=["device-enrollment"])

    @router.post("/enroll", response_model=DeviceEnrollmentResponse, status_code=201)
    def enroll(
        body: DeviceEnrollmentRequest,
        request: Request,
    ) -> DeviceEnrollmentResponse:
        remote_key = request.client.host if request.client is not None else "unknown"
        try:
            return service.enroll(body, remote_key=remote_key)
        except EnrollmentRateLimitError as error:
            raise HTTPException(
                status_code=429,
                detail={"code": "enrollment_rate_limited"},
            ) from error
        except PairingTokenNotUsableError as error:
            raise HTTPException(
                status_code=401,
                detail={"code": "pairing_failed"},
            ) from error
        except DeviceCertificateAuthorityError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "certificate_request_invalid"},
            ) from error

    return router
