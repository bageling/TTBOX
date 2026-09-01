/**
 * TTBOX API Client — 统一 API 请求层
 * 所有页面通过此 Client 访问后端，禁止直接 fetch/XMLHttpRequest
 */

const TTBOX = (function() {
  'use strict';

  const BASE = '';
  const TIMEOUT_MS = 15000;

  // ── 状态分类 ──
  const STATUS = {
    REAL: 'REAL',
    PARTIAL: 'PARTIAL',
    PLANNED: 'PLANNED'
  };

  // ── 错误类型 ──
  class ApiError extends Error {
    constructor(message, type, status) {
      super(message);
      this.name = 'ApiError';
      this.type = type || 'UNKNOWN';
      this.status = status || 0;
    }
  }

  // ── 核心请求 ──
  async function request(method, path, body, options = {}) {
    const url = BASE + path;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeout || TIMEOUT_MS);

    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json', ...options.headers },
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal
      });

      clearTimeout(timeout);

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        throw new ApiError(
          data.error || data.message || `请求失败 (${res.status})`,
          res.status >= 500 ? 'BACKEND_ERROR' : 'INVALID_PARAMETER',
          res.status
        );
      }

      if (data.ok === false) {
        throw new ApiError(
          data.error || '操作失败',
          'BACKEND_ERROR',
          res.status
        );
      }

      return data.data !== undefined ? data.data : data;

    } catch (err) {
      clearTimeout(timeout);
      if (err instanceof ApiError) throw err;
      if (err.name === 'AbortError') {
        throw new ApiError('请求超时，服务器未响应', 'TIMEOUT');
      }
      if (err.message?.includes('Failed to fetch') || err.message?.includes('NetworkError')) {
        throw new ApiError('服务器连接失败，请检查网络', 'NETWORK_ERROR');
      }
      throw new ApiError(err.message || '未知错误', 'UNKNOWN');
    }
  }

  // ── 快捷方法 ──
  const api = {
    get: (path, opts) => request('GET', path, null, opts),
    post: (path, body, opts) => request('POST', path, body, opts),
    put: (path, body, opts) => request('PUT', path, body, opts),
    del: (path, opts) => request('DELETE', path, null, opts),

    // ── 中文错误消息 ──
    getUserMessage(err) {
      const map = {
        'TIMEOUT': '服务器响应超时，请稍后重试',
        'NETWORK_ERROR': '无法连接到服务器，请检查网络连接',
        'BACKEND_ERROR': '服务器内部错误，请稍后重试',
        'INVALID_PARAMETER': '请求参数有误',
        'UNAUTHORIZED': '未授权，请重新登录',
        'NOT_IMPLEMENTED': '此功能尚未实现',
      };
      return map[err.type] || err.message || '操作失败，请重试';
    },

    // ── PLANNED 功能占位 ──
    planned(path) {
      console.warn(`[TTBOX] PLANNED API: ${path}`);
      return Promise.reject(new ApiError('此功能即将上线', 'NOT_IMPLEMENTED'));
    }
  };

  // ── 导出 ──
  return {
    api,
    ApiError,
    STATUS,
    request
  };
})();
