from pathlib import Path
from contextlib import asynccontextmanager
from io import BytesIO
from typing import AsyncIterator, Callable
import logging

from docling.datamodel.base_models import (
    ConversionStatus,
    DoclingComponentType,
    InputFormat,
)
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.document import DoclingDocument, PictureItem, PictureDescriptionData, PictureClassificationData
from docling_core.types.io import DocumentStream
from docling.datamodel.pipeline_options import smolvlm_picture_description
from docling.utils import model_downloader
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import uvicorn


from src.models import (
    OutputFormat,
    ParseFileRequest,
    ParseResponse,
    ParseResponseData,
    ParseUrlRequest,
)
from src.config import Config, get_log_config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Setup and teardown events of the app"""
    # Setup
    config = Config()
    picture_enrichment_options = smolvlm_picture_description
    picture_enrichment_options.prompt = "Describe the image in three sentences. Be consise and accurate. If it shows a table try to extract the data formated as markdown. Do not repeat the sentence the image shows"

    ocr_languages = config.ocr_languages.split(",")
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=PdfPipelineOptions(
                    ocr_options=EasyOcrOptions(lang=ocr_languages),
                    picture_description_options = picture_enrichment_options,
                    images_scale = 2.0,
                    generate_picture_images = True,
                    do_table_structure=True,
                    do_code_enrichment=True,
                    do_formula_enrichment=True,
                    do_picture_classification=True,
                    do_picture_description=True,
                )
            )
        }
    )
    for i, format in enumerate(InputFormat):
        logger.info(f"Initializing {format.value} pipeline {i + 1}/{len(InputFormat)}")

        converter.initialize_pipeline(format)

    app.state.converter = converter
    app.state.config = config

    yield
    # Teardown


app = FastAPI(lifespan=lifespan)

bearer_auth = HTTPBearer(auto_error=False)


async def authorize_header(
    request: Request, bearer: HTTPAuthorizationCredentials | None = Depends(bearer_auth)
) -> None:
    # Do nothing if AUTH_KEY is not set
    auth_token: str | None = request.app.state.config.auth_token
    if auth_token is None:
        return

    # Validate auth bearer
    if bearer is None or bearer.credentials != auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Unauthorized"},
        )


@app.exception_handler(Exception)
async def ingestion_error_handler(_, exc: Exception) -> None:
    detail = {"message": str(exc)}
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail
    )


ConvertData = str | Path | DocumentStream
ConvertFunc = Callable[[ConvertData], ConversionResult]


def convert(request: Request) -> ConvertFunc:
    def convert_func(data: ConvertData) -> ConversionResult:
        try:
            result = request.app.state.converter.convert(data, raises_on_error=False)
            _check_conversion_result(result)
            return result
        except FileNotFoundError as exc:
            logger.error(f"File not found error: {str(exc)}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"message": "Input not found"},
            ) from exc

    return convert_func


@app.post("/parse/url", response_model=ParseResponse)
def parse_document_url(
    payload: ParseUrlRequest,
    convert: ConvertFunc = Depends(convert),
    _=Depends(authorize_header),
) -> ParseResponse:
    try:
        result = convert(payload.url)
    except:
        return ParseResponse(
            message="Document not parsed",
            status="NOk",
            data=ParseResponseData(output="Ask Andreas Schiffler", json_output={}),
    )

    doc = result.document
    output = _get_output(doc, payload.output_format)
    json_output = doc.export_to_dict() if payload.include_json else None
    for element, _level in doc.iterate_items():
        if isinstance(element, PictureItem):
            logger.debug(element.self_ref)
            logger.debug(element.caption_text(doc=doc))
            for annotation in element.annotations:
                if isinstance(annotation,PictureClassificationData):
                    logger.debug(annotation.predicted_classes[0].class_name)
                if isinstance(annotation,PictureDescriptionData):
                    logger.debug(annotation.text)

    picture_data = []

    for element, _level in doc.iterate_items():
        if isinstance(element, PictureItem):
            picture_info = {
                "self_ref": element.self_ref,
                "caption_text": element.caption_text(doc=doc),
                "annotations": [],
            }
            for annotation in element.annotations:
                if isinstance(annotation, PictureClassificationData):
                    picture_info["annotations"].append({
                        "type": "classification",
                        "predicted_class": annotation.predicted_classes[0].class_name,
                    })
                if isinstance(annotation, PictureDescriptionData):
                    picture_info["annotations"].append({
                        "type": "description",
                        "text": annotation.text,
                    })
            picture_data.append(picture_info)

    json_output = json_output or {}
    json_output["picture_data"] = picture_data

    return ParseResponse(
        message="Document parsed successfully",
        status="Ok",
        data=ParseResponseData(output=output, json_output=json_output),
    )


@app.post("/parse/file", response_model=ParseResponse)
def parse_document_stream(
    file: UploadFile,
    convert: ConvertFunc = Depends(convert),
    payload: ParseFileRequest = Depends(ParseFileRequest.from_form_data),
    _=Depends(authorize_header),
) -> ParseResponse:
    binary_data = file.file.read()
    data = DocumentStream(
        name=file.filename or "unset_name", stream=BytesIO(binary_data)
    )
    try:
        result = convert(data)
    except:
        return ParseResponse(
            message="Document not parsed",
            status="NOk",
            data=ParseResponseData(output="Ask Andreas Schiffler", json_output={}),
    )

    doc = result.document
    output = _get_output(doc, payload.output_format)
    json_output = doc.export_to_dict() if payload.include_json else None
    for element, _level in doc.iterate_items():
        if isinstance(element, PictureItem):
            logger.debug(element.self_ref)
            logger.debug(element.caption_text(doc=doc))
            for annotation in element.annotations:
                if isinstance(annotation,PictureClassificationData):
                    logger.debug(annotation.predicted_classes[0].class_name)
                if isinstance(annotation,PictureDescriptionData):
                    logger.debug(annotation.text)
    picture_data = []

    for element, _level in doc.iterate_items():
        if isinstance(element, PictureItem):
            picture_info = {
                "self_ref": element.self_ref,
                "caption_text": element.caption_text(doc=doc),
                "annotations": [],
            }
            for annotation in element.annotations:
                if isinstance(annotation, PictureClassificationData):
                    picture_info["annotations"].append({
                        "type": "classification",
                        "predicted_class": annotation.predicted_classes[0].class_name,
                    })
                if isinstance(annotation, PictureDescriptionData):
                    picture_info["annotations"].append({
                        "type": "description",
                        "text": annotation.text,
                    })
            picture_data.append(picture_info)

    json_output = json_output or {}
    json_output["picture_data"] = picture_data

    return ParseResponse(
        message="Document parsed successfully",
        status="Ok",
        data=ParseResponseData(output=output, json_output=json_output),
    )


def _check_conversion_result(result: ConversionResult) -> None:
    """Raises HTTPException and logs on error"""
    if result.status in [ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS]:
        return

    if result.errors:
        for error in result.errors:
            if error.component_type == DoclingComponentType.USER_INPUT:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"message": error.error_message},
                )
            logger.error(
                f"Error in: {error.component_type.name} - {error.error_message}"
            )
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _get_output(document: DoclingDocument, format: OutputFormat) -> str:
    if format == OutputFormat.MARKDOWN:
        return document.export_to_markdown()
    if format == OutputFormat.TEXT:
        return document.export_to_text()
    if format == OutputFormat.HTML:
        return document.export_to_html()


if __name__ == "__main__":
    config = Config()
    model_downloader.download_models()
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=config.port,
        log_config=get_log_config(config.log_level),
        reload=config.dev_mode,
        workers=config.get_num_workers(),
    )
