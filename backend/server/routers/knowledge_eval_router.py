from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from server.utils.auth_middleware import get_admin_user
from yuxi.knowledge.eval.benchmark_generation import (
    DEFAULT_BENCHMARK_GENERATION_CONCURRENCY,
    MAX_BENCHMARK_GENERATION_CONCURRENCY,
)
from yuxi.knowledge.eval.service import EvaluationService
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger


evaluation = APIRouter(prefix="/evaluation", tags=["evaluation"])


class GenerateDatasetRequest(BaseModel):
    name: str = Field(default="自动生成评估数据集", min_length=1, max_length=100)
    description: str = ""
    count: int = Field(default=10, ge=1, le=100)
    neighbors_count: int = Field(default=1, ge=0, le=10)
    concurrency_count: int = Field(
        default=DEFAULT_BENCHMARK_GENERATION_CONCURRENCY,
        ge=1,
        le=MAX_BENCHMARK_GENERATION_CONCURRENCY,
    )
    llm_model_spec: str = Field(..., min_length=1)
    generation_mode: Literal["vector", "graph_enhanced"] = "vector"
    graph_expand_top_k: int = Field(default=1, ge=1, le=3)


class RunEvaluationRequest(BaseModel):
    dataset_id: str = Field(..., min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    retrieval_config: dict[str, Any] = Field(default_factory=dict, alias="model_config")


class ExternalBaselineProvenanceRequest(BaseModel):
    system_name: str = Field(..., min_length=1, max_length=120)
    system_version: str | None = Field(default=None, max_length=120)
    run_id: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)
    configuration: dict[str, Any] = Field(default_factory=dict)


class ExternalBaselineResultRequest(BaseModel):
    item_id: str = Field(..., min_length=1, max_length=64)
    query: str = Field(..., min_length=1, max_length=10000)
    generated_answer: str = Field(default="", max_length=200000)
    retrieved_document_hashes: list[str] = Field(default_factory=list, max_length=100)


class ExperimentVariantRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    kb_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description="留空时使用实验所属知识库；仅可选择具有相同已索引语料的对照知识库。",
    )
    retrieval_config: dict[str, Any] = Field(default_factory=dict)
    execution_type: Literal["research_compass", "external_rag", "direct_llm"] = "research_compass"
    provenance: ExternalBaselineProvenanceRequest | None = None
    dataset_fingerprint: str | None = Field(default=None, max_length=128)
    corpus_fingerprint: str | None = Field(default=None, max_length=128)
    external_results: list[ExternalBaselineResultRequest] = Field(default_factory=list, max_length=1000)


class CreateExperimentRequest(BaseModel):
    dataset_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    baseline_variant_index: int = Field(default=0, ge=0)
    shared_config: dict[str, Any] = Field(default_factory=dict)
    variants: list[ExperimentVariantRequest] = Field(..., min_length=2, max_length=8)


@evaluation.post("/databases/{kb_id}/datasets/upload")
async def upload_evaluation_dataset(
    kb_id: str,
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
    current_user: User = Depends(get_admin_user),
):
    """上传评估数据集"""
    try:
        if not file.filename.endswith(".jsonl"):
            raise HTTPException(status_code=400, detail="仅支持JSONL格式文件")

        service = EvaluationService()
        result = await service.upload_dataset(
            kb_id=kb_id,
            file_content=await file.read(),
            filename=file.filename,
            name=name,
            description=description,
            created_by=current_user.uid,
        )
        return {"message": "success", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"上传评估数据集失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传评估数据集失败: {str(e)}")


@evaluation.get("/databases/{kb_id}/datasets")
async def list_evaluation_datasets(kb_id: str, current_user: User = Depends(get_admin_user)):
    """获取知识库的评估数据集列表"""
    try:
        service = EvaluationService()
        datasets = await service.list_datasets(kb_id)
        return {"message": "success", "data": datasets}
    except Exception as e:
        logger.exception(f"获取评估数据集列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取评估数据集列表失败: {str(e)}")


@evaluation.get("/databases/{kb_id}/datasets/{dataset_id}")
async def get_evaluation_dataset(
    kb_id: str, dataset_id: str, page: int = 1, page_size: int = 10, current_user: User = Depends(get_admin_user)
):
    """获取评估数据集详情"""
    try:
        if page < 1:
            raise HTTPException(status_code=400, detail="页码必须大于0")
        if page_size < 1 or page_size > 100:
            raise HTTPException(status_code=400, detail="每页大小必须在1-100之间")

        service = EvaluationService()
        dataset = await service.get_dataset_detail(kb_id, dataset_id, page, page_size)
        return {"message": "success", "data": dataset}
    except HTTPException:
        raise
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"获取评估数据集详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取评估数据集详情失败: {str(e)}")


@evaluation.get("/datasets/{dataset_id}/download")
async def download_evaluation_dataset(dataset_id: str, current_user: User = Depends(get_admin_user)):
    """导出评估数据集 JSONL"""
    try:
        service = EvaluationService()
        export_info = await service.export_dataset_jsonl(dataset_id)
        filename = export_info["filename"]
        return Response(
            content=export_info["content"].encode("utf-8"),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"导出评估数据集失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出评估数据集失败: {str(e)}")


@evaluation.get("/databases/{kb_id}/datasets/{dataset_id}/baseline-template")
async def download_external_baseline_template(
    kb_id: str, dataset_id: str, current_user: User = Depends(get_admin_user)
):
    """导出带数据集和语料指纹的外部基线结果模板。"""
    try:
        export_info = await EvaluationService().export_external_baseline_template(kb_id, dataset_id)
        return Response(
            content=export_info["content"].encode("utf-8"),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(export_info['filename'])}"},
        )
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"导出外部基线模板失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出外部基线模板失败: {str(e)}")


@evaluation.delete("/datasets/{dataset_id}")
async def delete_evaluation_dataset(dataset_id: str, current_user: User = Depends(get_admin_user)):
    """删除评估数据集"""
    try:
        service = EvaluationService()
        await service.delete_dataset(dataset_id)
        return {"message": "success", "data": None}
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"删除评估数据集失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除评估数据集失败: {str(e)}")


@evaluation.post("/databases/{kb_id}/datasets/generate")
async def generate_evaluation_dataset(
    kb_id: str, request: GenerateDatasetRequest, current_user: User = Depends(get_admin_user)
):
    """自动生成评估数据集"""
    try:
        service = EvaluationService()
        result = await service.generate_dataset(
            kb_id=kb_id,
            name=request.name,
            description=request.description,
            count=request.count,
            neighbors_count=request.neighbors_count,
            concurrency_count=request.concurrency_count,
            llm_model_spec=request.llm_model_spec,
            generation_mode=request.generation_mode,
            graph_expand_top_k=request.graph_expand_top_k,
            created_by=current_user.uid,
        )
        return {"message": "success", "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"生成评估数据集失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成评估数据集失败: {str(e)}")


@evaluation.post("/databases/{kb_id}/runs")
async def run_evaluation(kb_id: str, request: RunEvaluationRequest, current_user: User = Depends(get_admin_user)):
    """运行RAG评估"""
    try:
        service = EvaluationService()
        run_id = await service.run_evaluation(
            kb_id=kb_id,
            dataset_id=request.dataset_id,
            name=request.name,
            model_config=request.retrieval_config,
            created_by=current_user.uid,
        )
        return {"message": "success", "data": {"run_id": run_id}}
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"启动评估失败: {e}")
        raise HTTPException(status_code=500, detail=f"启动评估失败: {str(e)}")


@evaluation.get("/databases/{kb_id}/runs")
async def list_evaluation_runs(kb_id: str, current_user: User = Depends(get_admin_user)):
    """获取知识库评估运行历史"""
    try:
        service = EvaluationService()
        runs = await service.list_runs(kb_id)
        return {"message": "success", "data": runs}
    except Exception as e:
        logger.exception(f"获取评估运行历史失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取评估运行历史失败: {str(e)}")


@evaluation.get("/databases/{kb_id}/runs/{run_id}")
async def get_evaluation_run_results(
    kb_id: str,
    run_id: str,
    page: int = 1,
    page_size: int = 20,
    error_only: bool = False,
    current_user: User = Depends(get_admin_user),
):
    """获取评估运行结果"""
    try:
        if page < 1:
            raise HTTPException(status_code=400, detail="页码必须大于0")
        if page_size < 1 or page_size > 100:
            raise HTTPException(status_code=400, detail="每页大小必须在1-100之间")

        service = EvaluationService()
        results = await service.get_run_results(kb_id, run_id, page=page, page_size=page_size, error_only=error_only)
        return {"message": "success", "data": results}
    except HTTPException:
        raise
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"获取评估运行结果失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取评估运行结果失败: {str(e)}")


@evaluation.delete("/databases/{kb_id}/runs/{run_id}")
async def delete_evaluation_run(kb_id: str, run_id: str, current_user: User = Depends(get_admin_user)):
    """删除评估运行"""
    try:
        service = EvaluationService()
        await service.delete_run(kb_id, run_id)
        return {"message": "success", "data": None}
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"删除评估运行失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除评估运行失败: {str(e)}")


@evaluation.post("/databases/{kb_id}/experiments")
async def create_evaluation_experiment(
    kb_id: str, request: CreateExperimentRequest, current_user: User = Depends(get_admin_user)
):
    """以同一评估基准和共享模型条件运行一组检索消融实验。"""
    try:
        result = await EvaluationService().create_experiment(
            kb_id=kb_id,
            dataset_id=request.dataset_id,
            name=request.name,
            description=request.description,
            baseline_variant_index=request.baseline_variant_index,
            shared_config=request.shared_config,
            variants=[item.model_dump() for item in request.variants],
            created_by=str(current_user.uid),
        )
        return {"message": "success", "data": result}
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"创建消融实验失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建消融实验失败: {str(e)}")


@evaluation.get("/databases/{kb_id}/experiments")
async def list_evaluation_experiments(kb_id: str, current_user: User = Depends(get_admin_user)):
    try:
        return {"message": "success", "data": await EvaluationService().list_experiments(kb_id)}
    except Exception as e:
        logger.exception(f"获取消融实验列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取消融实验列表失败: {str(e)}")


@evaluation.get("/databases/{kb_id}/experiments/{experiment_id}")
async def get_evaluation_experiment(kb_id: str, experiment_id: str, current_user: User = Depends(get_admin_user)):
    try:
        return {"message": "success", "data": await EvaluationService().get_experiment(kb_id, experiment_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"获取消融实验失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取消融实验失败: {str(e)}")


@evaluation.delete("/databases/{kb_id}/experiments/{experiment_id}")
async def delete_evaluation_experiment(kb_id: str, experiment_id: str, current_user: User = Depends(get_admin_user)):
    try:
        await EvaluationService().delete_experiment(kb_id, experiment_id)
        return {"message": "success", "data": None}
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception(f"删除消融实验失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除消融实验失败: {str(e)}")
