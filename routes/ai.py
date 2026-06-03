"""Flask Blueprint - AI分析路由 (含Dexter AI)"""

from __future__ import annotations

import json
import threading
from flask import Blueprint, request, render_template, Response, stream_with_context

from modules.api_response import api_success, api_error
from modules.logger import log

ai_bp = Blueprint("ai", __name__)


@ai_bp.route("/dexter")
def dexter_page():
    return render_template("dexter_ai.html")


@ai_bp.route("/api/ai/analyze", methods=["POST"])
def api_ai_analyze():
    try:
        data = request.get_json(silent=True)
        if not data:
            return api_error("请求体不能为空")
        code = data.get("code", "").strip()
        if not code:
            return api_error("股票代码不能为空")
        from modules.ai_analyzer import analyze_stock
        result = analyze_stock(code)
        return api_success(result)
    except Exception as e:
        log.error(f"AI分析失败: {e}", exc_info=True)
        return api_error(f"分析失败: {e}")


@ai_bp.route("/api/dexter/chat", methods=["POST"])
def api_dexter_chat():
    """Dexter AI 对话（SSE 流式响应）"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return api_error("请求体不能为空")

        query = data.get("query") or data.get("message", "")
        if not query:
            return api_error("消息不能为空")

        override_model = data.get("model")
        override_base_url = data.get("base_url")
        override_api_key = data.get("api_key")
        conversation_history = data.get("history")

        try:
            from modules.dexter.agent import DexterAgent, AgentConfig
            from modules.dexter.llm import create_llm_client
        except ImportError as e:
            log.error(f"Dexter模块导入失败: {e}", exc_info=True)
            resp = api_success({"response": f"Dexter AI 模块暂未启用（{e}），当前仅支持基础选股功能。"})
            return resp

        def build_agent():
            if override_model or override_base_url or override_api_key:
                llm_kwargs = {}
                if override_model:
                    llm_kwargs["model"] = override_model
                if override_base_url:
                    llm_kwargs["base_url"] = override_base_url
                if override_api_key:
                    llm_kwargs["api_key"] = override_api_key
                llm = create_llm_client(**llm_kwargs)
                config = AgentConfig()
                agent = DexterAgent(config)
                agent.llm = llm
                return agent
            else:
                from modules.dexter.agent import _get_default_agent
                return _get_default_agent()

        def generate():
            try:
                agent = build_agent()
            except Exception as e:
                log.error(f"Dexter Agent构建失败: {e}", exc_info=True)
                yield f"event: error\ndata: {json.dumps({'message': f'Agent构建失败: {e}'}, ensure_ascii=False)}\n\n"
                return

            event_queue = []
            done_flag = [False]
            result_holder = [None]

            def on_event(event_type, event_data):
                event_queue.append({"type": event_type, "data": event_data})

            def run_agent():
                try:
                    result = agent.run(query, conversation_history, on_event=on_event)
                    result_holder[0] = result
                except Exception as e:
                    event_queue.append({"type": "error", "data": {"message": str(e)}})
                finally:
                    done_flag[0] = True

            thread = threading.Thread(target=run_agent, daemon=True)
            thread.start()

            import queue
            flush_idx = 0
            while not done_flag[0] or flush_idx < len(event_queue):
                while flush_idx < len(event_queue):
                    evt = event_queue[flush_idx]
                    flush_idx += 1
                    evt_type = evt["type"]
                    evt_data = evt["data"]

                    if evt_type == "error":
                        yield f"event: error\ndata: {json.dumps({'message': evt_data.get('message', '未知错误')}, ensure_ascii=False)}\n\n"
                        return

                    if evt_type == "thinking":
                        yield f"event: thinking\ndata: {json.dumps({}, ensure_ascii=False)}\n\n"
                    elif evt_type == "tool_start":
                        tool_name = evt_data.get("tool", "")
                        yield f"event: tool_start\ndata: {json.dumps({'tool': tool_name}, ensure_ascii=False)}\n\n"
                    elif evt_type == "tool_end":
                        yield f"event: tool_end\ndata: {json.dumps({}, ensure_ascii=False)}\n\n"
                    elif evt_type == "status":
                        yield f"event: status\ndata: {json.dumps({'message': evt_data.get('message', '')}, ensure_ascii=False)}\n\n"
                    elif evt_type == "iteration":
                        yield f"event: iteration\ndata: {json.dumps({'iteration': evt_data.get('iteration', 1), 'max': evt_data.get('max', 5)}, ensure_ascii=False)}\n\n"
                    elif evt_type == "microcompact":
                        cleared = evt_data.get("cleared", 0)
                        yield f"event: status\ndata: {json.dumps({'message': f'上下文微压缩: 清除{cleared}条旧消息'}, ensure_ascii=False)}\n\n"
                    elif evt_type == "context_cleared":
                        cleared_count = evt_data.get("cleared_count", 0)
                        yield f"event: status\ndata: {json.dumps({'message': f'上下文溢出恢复: 清除{cleared_count}条消息'}, ensure_ascii=False)}\n\n"

                if done_flag[0]:
                    break
                thread.join(timeout=0.2)

            result = result_holder[0]
            if result and result.get("answer"):
                yield f"event: done\ndata: {json.dumps({'answer': result['answer']}, ensure_ascii=False)}\n\n"
            elif result is None:
                yield f"event: error\ndata: {json.dumps({'message': 'Agent执行异常，未返回结果'}, ensure_ascii=False)}\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as e:
        log.error(f"Dexter聊天失败: {e}", exc_info=True)
        return api_error(f"对话失败: {e}")


@ai_bp.route("/api/dexter/status")
def api_dexter_status():
    """Dexter AI 状态"""
    try:
        try:
            from modules.dexter.agent import get_status
            from modules.dexter.tools.registry import get_all_tools
            status = get_status()
            tools = get_all_tools()
            tool_names = [t.name for t in tools]
            return api_success({
                "available": status.get("enabled", False),
                "status": status.get("status", "unknown"),
                "model": status.get("model", ""),
                "provider": status.get("provider", ""),
                "base_url": status.get("base_url", ""),
                "has_api_key": status.get("has_api_key", False),
                "tools": tool_names,
                "message": status.get("message", ""),
            })
        except ImportError as e:
            log.error(f"Dexter模块导入失败: {e}", exc_info=True)
            return api_success({
                "available": False,
                "status": "unavailable",
                "tools": [],
                "error": f"Dexter AI 模块未启用: {e}",
            })
        except Exception as e:
            log.error(f"Dexter模块初始化失败: {e}", exc_info=True)
            return api_success({
                "available": False,
                "status": "error",
                "tools": [],
                "error": f"Dexter初始化失败: {type(e).__name__}: {e}",
            })
    except Exception as e:
        log.error(f"获取Dexter状态失败: {e}", exc_info=True)
        return api_error(f"获取状态失败: {e}")
