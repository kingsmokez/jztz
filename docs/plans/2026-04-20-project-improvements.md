# jztz_v17 项目改进实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 对 jztz_v17 项目进行全面质量改进——引入测试框架、修复错误处理、重组项目结构、添加环境变量体系、拆分大组件、优化 Zustand selector、补充架构文档。

**Architecture:** 前端 React+TS (temp_repo) 采用 Vitest+RTL 测试框架；项目结构将 temp_repo 重命名为 frontend；环境配置引入 .env 体系；组件拆分使用自定义 hooks 模式；Zustand selector 合理合并减少重渲染。

**Tech Stack:** React 19, TypeScript 5.9, Vite 7, Zustand 5, Vitest, React Testing Library, ESLint 9, Prettier

---

## Task 1: P0 — 引入 Vitest 测试框架 + 关键路径测试

**Files:**
- Create: `temp_repo/vitest.config.ts`
- Create: `temp_repo/src/__tests__/setup.ts`
- Create: `temp_repo/src/__tests__/utils/format.test.ts`
- Create: `temp_repo/src/__tests__/router/ProtectedRoute.test.tsx`
- Create: `temp_repo/src/__tests__/stores/useThemeStore.test.ts`
- Create: `temp_repo/src/__tests__/stores/useLanguageStore.test.ts`
- Modify: `temp_repo/package.json` (添加 vitest 依赖和 test script)
- Modify: `temp_repo/tsconfig.json` (添加 vitest types)

**Step 1: 安装 Vitest 和 React Testing Library 依赖**

```bash
cd temp_repo
npm install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom @vitest/coverage-v8
```

**Step 2: 创建 Vitest 配置**

创建 `temp_repo/vitest.config.ts`:

```typescript
import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/__tests__/setup.ts'],
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: ['node_modules/', 'src/__tests__/', '**/*.d.ts', '**/*.config.*', '**/mockData'],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
```

**Step 3: 创建测试 setup 文件**

创建 `temp_repo/src/__tests__/setup.ts`:

```typescript
import '@testing-library/jest-dom';
```

**Step 4: 添加 tsconfig vitest 类型**

在 `temp_repo/tsconfig.json` 的 compilerOptions 中添加:

```json
"types": ["vitest/globals"]
```

同时修改 include 以包含测试文件:

```json
"include": ["src", "vite-env.d.ts"]
```

**Step 5: 添加 test script 到 package.json**

在 `temp_repo/package.json` scripts 中添加:

```json
"test": "vitest run",
"test:watch": "vitest",
"test:coverage": "vitest run --coverage"
```

**Step 6: 编写 format.ts 工具函数测试**

创建 `temp_repo/src/__tests__/utils/format.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { maskApiKey, formatFileSize, formatDateTime, formatUnixTimestamp, formatNumber, truncateText } from '@/utils/format';

describe('maskApiKey', () => {
  it('should mask a standard API key', () => {
    const result = maskApiKey('ABCDEFGHIJKLMNOP');
    expect(result).toBe('AB******op');
  });

  it('should return empty string for empty input', () => {
    expect(maskApiKey('')).toBe('');
    expect(maskApiKey('   ')).toBe('');
  });

  it('should handle very short keys (< 4 chars)', () => {
    const result = maskApiKey('AB');
    expect(result.startsWith('A')).toBe(true);
    expect(result.endsWith('B')).toBe(true);
  });

  it('should handle 4-char keys', () => {
    const result = maskApiKey('ABCD');
    expect(result).toBe('AB****CD');
  });
});

describe('formatFileSize', () => {
  it('should format 0 bytes', () => {
    expect(formatFileSize(0)).toBe('0 B');
  });

  it('should format kilobytes', () => {
    expect(formatFileSize(1024)).toBe('1.00 KB');
  });

  it('should format megabytes', () => {
    expect(formatFileSize(1048576)).toBe('1.00 MB');
  });
});

describe('formatDateTime', () => {
  it('should return Invalid Date for invalid input', () => {
    expect(formatDateTime('not-a-date')).toBe('Invalid Date');
  });

  it('should format a valid date string', () => {
    const result = formatDateTime('2024-01-01T00:00:00Z', 'en-US');
    expect(result).toBeTruthy();
    expect(result).not.toBe('Invalid Date');
  });

  it('should format a Date object', () => {
    const result = formatDateTime(new Date('2024-06-15'), 'en-US');
    expect(result).toBeTruthy();
  });
});

describe('formatUnixTimestamp', () => {
  it('should return empty string for null/undefined/empty', () => {
    expect(formatUnixTimestamp(null)).toBe('');
    expect(formatUnixTimestamp(undefined)).toBe('');
    expect(formatUnixTimestamp('')).toBe('');
  });

  it('should handle seconds (10-digit)', () => {
    const result = formatUnixTimestamp(1704067200);
    expect(result).toBeTruthy();
  });

  it('should handle milliseconds (13-digit)', () => {
    const result = formatUnixTimestamp(1704067200000);
    expect(result).toBeTruthy();
  });

  it('should handle string numeric input', () => {
    const result = formatUnixTimestamp('1704067200');
    expect(result).toBeTruthy();
  });

  it('should return empty for NaN numeric input', () => {
    expect(formatUnixTimestamp('abc')).toBe('');
  });
});

describe('formatNumber', () => {
  it('should format numbers with locale', () => {
    const result = formatNumber(1234567, 'en-US');
    expect(result).toBeTruthy();
  });
});

describe('truncateText', () => {
  it('should not truncate short text', () => {
    expect(truncateText('hello', 10)).toBe('hello');
  });

  it('should truncate long text', () => {
    expect(truncateText('hello world', 5)).toBe('hello...');
  });
});
```

**Step 7: 编写 ProtectedRoute 测试**

创建 `temp_repo/src/__tests__/router/ProtectedRoute.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ProtectedRoute } from '@/router/ProtectedRoute';
import { useAuthStore } from '@/stores';

// Mock the auth store
vi.mock('@/stores', () => ({
  useAuthStore: vi.fn(),
}));

// Mock LoadingSpinner
vi.mock('@/components/ui/LoadingSpinner', () => ({
  LoadingSpinner: () => <div data-testid="loading-spinner">Loading...</div>,
}));

const mockUseAuthStore = vi.mocked(useAuthStore);

describe('ProtectedRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should redirect to /login when not authenticated', () => {
    mockUseAuthStore.mockImplementation((selector: any) => {
      const state = {
        isAuthenticated: false,
        managementKey: '',
        apiBase: '',
        checkAuth: vi.fn(),
      };
      return selector(state);
    });

    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <ProtectedRoute>
          <div>Protected Content</div>
        </ProtectedRoute>
      </MemoryRouter>
    );

    expect(screen.queryByText('Protected Content')).toBeNull();
  });

  it('should render children when authenticated', () => {
    mockUseAuthStore.mockImplementation((selector: any) => {
      const state = {
        isAuthenticated: true,
        managementKey: 'test-key',
        apiBase: 'http://localhost:8317',
        checkAuth: vi.fn().mockResolvedValue(true),
      };
      return selector(state);
    });

    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <ProtectedRoute>
          <div>Protected Content</div>
        </ProtectedRoute>
      </MemoryRouter>
    );

    expect(screen.getByText('Protected Content')).toBeInTheDocument();
  });

  it('should show loading spinner while checking auth', () => {
    mockUseAuthStore.mockImplementation((selector: any) => {
      const state = {
        isAuthenticated: false,
        managementKey: 'test-key',
        apiBase: 'http://localhost:8317',
        checkAuth: vi.fn().mockImplementation(() => new Promise(() => {})), // never resolves
      };
      return selector(state);
    });

    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <ProtectedRoute>
          <div>Protected Content</div>
        </ProtectedRoute>
      </MemoryRouter>
    );

    expect(screen.getByTestId('loading-spinner')).toBeInTheDocument();
  });
});
```

**Step 8: 编写 useThemeStore 测试**

创建 `temp_repo/src/__tests__/stores/useThemeStore.test.ts`:

```typescript
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useThemeStore } from '@/stores/useThemeStore';

describe('useThemeStore', () => {
  beforeEach(() => {
    // Reset store state between tests
    useThemeStore.setState({
      theme: 'auto',
    });
  });

  it('should have default theme as auto', () => {
    expect(useThemeStore.getState().theme).toBe('auto');
  });

  it('should set theme to dark', () => {
    useThemeStore.getState().setTheme('dark');
    expect(useThemeStore.getState().theme).toBe('dark');
  });

  it('should set theme to light', () => {
    useThemeStore.getState().setTheme('light');
    expect(useThemeStore.getState().theme).toBe('light');
  });

  it('should set theme to white', () => {
    useThemeStore.getState().setTheme('white');
    expect(useThemeStore.getState().theme).toBe('white');
  });
});
```

**Step 9: 编写 useLanguageStore 测试**

创建 `temp_repo/src/__tests__/stores/useLanguageStore.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from 'vitest';
import { useLanguageStore } from '@/stores/useLanguageStore';

describe('useLanguageStore', () => {
  beforeEach(() => {
    useLanguageStore.setState({
      language: 'zh-CN',
    });
  });

  it('should have default language zh-CN', () => {
    expect(useLanguageStore.getState().language).toBe('zh-CN');
  });

  it('should set language to en', () => {
    useLanguageStore.getState().setLanguage('en');
    expect(useLanguageStore.getState().language).toBe('en');
  });

  it('should set language to ru', () => {
    useLanguageStore.getState().setLanguage('ru');
    expect(useLanguageStore.getState().language).toBe('ru');
  });
});
```

**Step 10: 运行测试确认通过**

```bash
cd temp_repo && npm test
```

Expected: All tests PASS

**Step 11: Commit**

```bash
git add temp_repo/vitest.config.ts temp_repo/src/__tests__/ temp_repo/package.json temp_repo/package-lock.json temp_repo/tsconfig.json
git commit -m "feat: introduce Vitest testing framework with initial test suite"
```

---

## Task 2: P1 — ProtectedRoute 错误处理修复

**Files:**
- Modify: `temp_repo/src/router/ProtectedRoute.tsx`

**Step 1: 修复 ProtectedRoute 的未捕获异常**

当前代码 (line 18-20):
```typescript
try {
  await checkAuth();
} finally {
  setChecking(false);
}
```

修改为添加 catch 处理:

```typescript
try {
  await checkAuth();
} catch {
  // 鉴权失败时静默处理——store 已在 checkAuth 中设置 isAuthenticated=false
  // 无需额外操作，ProtectedRoute 将自动重定向到 /login
} finally {
  setChecking(false);
}
```

**Step 2: 运行测试确认 ProtectedRoute 测试通过**

```bash
cd temp_repo && npm test -- src/__tests__/router/ProtectedRoute.test.tsx
```

Expected: PASS

**Step 3: Commit**

```bash
git add temp_repo/src/router/ProtectedRoute.tsx
git commit -m "fix: add catch handler in ProtectedRoute to prevent unhandled promise rejection"
```

---

## Task 3: P1 — 环境变量 .env 体系

**Files:**
- Create: `temp_repo/.env`
- Create: `temp_repo/.env.example`
- Create: `temp_repo/.env.production`
- Modify: `temp_repo/vite.config.ts` (添加 env 变量使用)
- Modify: `temp_repo/src/utils/connection.ts` (使用环境变量)
- Modify: `temp_repo/.gitignore` (排除 .env.local)

**Step 1: 创建 .env 文件**

创建 `temp_repo/.env`:

```env
# 开发环境配置
VITE_API_BASE=http://localhost:8317
VITE_DEFAULT_API_PORT=8317
```

创建 `temp_repo/.env.production`:

```env
# 生产环境配置 — API 基地址从浏览器 location 自动检测
# 如果需要指定固定地址，取消注释以下行：
# VITE_API_BASE=https://your-server.com
VITE_DEFAULT_API_PORT=8317
```

创建 `temp_repo/.env.example`:

```env
# 环境变量配置示例
# 复制此文件为 .env.local 进行本地配置（.env.local 不会被 git 追踪）

# API 服务器基地址（留空则从浏览器 location 自动检测）
VITE_API_BASE=

# API 默认端口
VITE_DEFAULT_API_PORT=8317
```

**Step 2: 修改 connection.ts 使用环境变量**

当前 `detectApiBaseFromLocation` 函数没有使用环境变量。修改 `temp_repo/src/utils/connection.ts`:

```typescript
import { DEFAULT_API_PORT, MANAGEMENT_API_PREFIX } from './constants';

// 环境变量优先级高于默认值
const ENV_API_PORT = Number(import.meta.env.VITE_DEFAULT_API_PORT) || DEFAULT_API_PORT;
const ENV_API_BASE = import.meta.env.VITE_API_BASE as string | undefined;

export const normalizeApiBase = (input: string): string => {
  let base = (input || '').trim();
  if (!base) return '';
  base = base.replace(/\/?v0\/management\/?$/i, '');
  base = base.replace(/\/+$/i, '');
  if (!/^https?:\/\//i.test(base)) {
    base = `http://${base}`;
  }
  return base;
};

export const computeApiUrl = (base: string): string => {
  const normalized = normalizeApiBase(base);
  if (!normalized) return '';
  return `${normalized}${MANAGEMENT_API_PREFIX}`;
};

export const detectApiBaseFromLocation = (): string => {
  // 1. 环境变量优先
  if (ENV_API_BASE) {
    return normalizeApiBase(ENV_API_BASE);
  }

  // 2. 从浏览器 location 检测
  try {
    const { protocol, hostname, port } = window.location;
    const normalizedPort = port ? `:${port}` : '';
    return normalizeApiBase(`${protocol}//${hostname}${normalizedPort}`);
  } catch {
    // 3. 回退到 localhost + 默认端口
    return normalizeApiBase(`http://localhost:${ENV_API_PORT}`);
  }
};

export const isLocalhost = (hostname: string): boolean => {
  const value = (hostname || '').toLowerCase();
  return value === 'localhost' || value === '127.0.0.1' || value === '[::1]';
};
```

**Step 3: 更新 .gitignore**

在 `temp_repo/.gitignore` 中添加:

```
# 环境变量 — 本地配置不应被追踪
.env.local
.env.*.local
```

**Step 4: 运行测试确认通过**

```bash
cd temp_repo && npm test
```

**Step 5: Commit**

```bash
git add temp_repo/.env temp_repo/.env.example temp_repo/.env.production temp_repo/src/utils/connection.ts temp_repo/.gitignore
git commit -m "feat: introduce environment variable system with .env files for multi-environment support"
```

---

## Task 4: P2 — DashboardPage 组件拆分 + 自定义 hook

**Files:**
- Create: `temp_repo/src/hooks/useDashboardData.ts`
- Create: `temp_repo/src/pages/DashboardPage/QuickStatsGrid.tsx`
- Create: `temp_repo/src/pages/DashboardPage/ConfigPills.tsx`
- Create: `temp_repo/src/pages/DashboardPage/HeroSection.tsx`
- Create: `temp_repo/src/pages/DashboardPage/index.ts`
- Modify: `temp_repo/src/pages/DashboardPage.tsx` → 拆分为以上组件

**Step 1: 创建 useDashboardData 自定义 hook**

创建 `temp_repo/src/hooks/useDashboardData.ts`:

```typescript
import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuthStore, useConfigStore, useModelsStore } from '@/stores';
import { apiKeysApi, providersApi, authFilesApi } from '@/services/api';

interface DashboardStats {
  apiKeys: number | null;
  authFiles: number | null;
}

interface ProviderStats {
  gemini: number | null;
  codex: number | null;
  claude: number | null;
  openai: number | null;
}

interface UseDashboardDataReturn {
  stats: DashboardStats;
  providerStats: ProviderStats;
  loading: boolean;
  providerStatsReady: boolean;
  hasProviderStats: boolean;
  totalProviderKeys: number;
  models: any[];
  modelsLoading: boolean;
}

export function useDashboardData(): UseDashboardDataReturn {
  const connectionStatus = useAuthStore((state) => state.connectionStatus);
  const apiBase = useAuthStore((state) => state.apiBase);
  const config = useConfigStore((state) => state.config);
  const models = useModelsStore((state) => state.models);
  const modelsLoading = useModelsStore((state) => state.loading);
  const fetchModelsFromStore = useModelsStore((state) => state.fetchModels);

  const [stats, setStats] = useState<DashboardStats>({ apiKeys: null, authFiles: null });
  const [providerStats, setProviderStats] = useState<ProviderStats>({
    gemini: null, codex: null, claude: null, openai: null
  });
  const [loading, setLoading] = useState(true);
  const apiKeysCache = useRef<string[]>([]);

  useEffect(() => {
    apiKeysCache.current = [];
  }, [apiBase, config?.apiKeys]);

  const resolveApiKeysForModels = useCallback(async () => {
    if (apiKeysCache.current.length) return apiKeysCache.current;
    const configKeys = normalizeApiKeyList(config?.apiKeys);
    if (configKeys.length) {
      apiKeysCache.current = configKeys;
      return configKeys;
    }
    try {
      const list = await apiKeysApi.list();
      const normalized = normalizeApiKeyList(list);
      if (normalized.length) apiKeysCache.current = normalized;
      return normalized;
    } catch {
      return [];
    }
  }, [config?.apiKeys]);

  const fetchModels = useCallback(async () => {
    if (connectionStatus !== 'connected' || !apiBase) return;
    try {
      const apiKeys = await resolveApiKeysForModels();
      await fetchModelsFromStore(apiBase, apiKeys[0]);
    } catch {
      // Ignore model fetch errors on dashboard
    }
  }, [connectionStatus, apiBase, resolveApiKeysForModels, fetchModelsFromStore]);

  useEffect(() => {
    const fetchStats = async () => {
      setLoading(true);
      try {
        const results = await Promise.allSettled([
          apiKeysApi.list(),
          authFilesApi.list(),
          providersApi.getGeminiKeys(),
          providersApi.getCodexConfigs(),
          providersApi.getClaudeConfigs(),
          providersApi.getOpenAIProviders()
        ]);

        setStats({
          apiKeys: results[0].status === 'fulfilled' ? results[0].value.length : null,
          authFiles: results[1].status === 'fulfilled' ? results[1].value.files.length : null
        });
        setProviderStats({
          gemini: results[2].status === 'fulfilled' ? results[2].value.length : null,
          codex: results[3].status === 'fulfilled' ? results[3].value.length : null,
          claude: results[4].status === 'fulfilled' ? results[4].value.length : null,
          openai: results[5].status === 'fulfilled' ? results[5].value.length : null
        });
      } finally {
        setLoading(false);
      }
    };

    if (connectionStatus === 'connected') {
      fetchStats();
      fetchModels();
    } else {
      setLoading(false);
    }
  }, [connectionStatus, fetchModels]);

  const providerStatsReady =
    providerStats.gemini !== null && providerStats.codex !== null &&
    providerStats.claude !== null && providerStats.openai !== null;
  const hasProviderStats =
    providerStats.gemini !== null || providerStats.codex !== null ||
    providerStats.claude !== null || providerStats.openai !== null;
  const totalProviderKeys = providerStatsReady
    ? (providerStats.gemini ?? 0) + (providerStats.codex ?? 0) +
      (providerStats.claude ?? 0) + (providerStats.openai ?? 0)
    : 0;

  return {
    stats, providerStats, loading,
    providerStatsReady, hasProviderStats, totalProviderKeys,
    models, modelsLoading
  };
}

// 内部辅助函数 — API Key 列表标准化
function normalizeApiKeyList(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
  const seen = new Set<string>();
  const keys: string[] = [];
  input.forEach((item) => {
    const record = item !== null && typeof item === 'object' && !Array.isArray(item)
      ? (item as Record<string, unknown>) : null;
    const value = typeof item === 'string' ? item
      : record ? (record['api-key'] ?? record['apiKey'] ?? record.key ?? record.Key) : '';
    const trimmed = String(value ?? '').trim();
    if (!trimmed || seen.has(trimmed)) return;
    seen.add(trimmed);
    keys.push(trimmed);
  });
  return keys;
}
```

**Step 2: 创建 HeroSection 子组件**

创建 `temp_repo/src/pages/DashboardPage/HeroSection.tsx` (提取 DashboardPage 的 hero 部分)

**Step 3: 创建 QuickStatsGrid 子组件**

创建 `temp_repo/src/pages/DashboardPage/QuickStatsGrid.tsx` (提取 bento stats grid 部分)

**Step 4: 创建 ConfigPills 子组件**

创建 `temp_repo/src/pages/DashboardPage/ConfigPills.tsx` (提取 config pills 部分)

**Step 5: 创建 index barrel export**

创建 `temp_repo/src/pages/DashboardPage/index.ts`:

```typescript
export { DashboardPage } from './DashboardPage';
```

**Step 6: 重写 DashboardPage 主组件**

将 `temp_repo/src/pages/DashboardPage.tsx` 简化为使用子组件和 hook:

```typescript
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores';
import { useDashboardData } from '@/hooks/useDashboardData';
import { HeroSection } from './DashboardPage/HeroSection';
import { QuickStatsGrid } from './DashboardPage/QuickStatsGrid';
import { ConfigPills } from './DashboardPage/ConfigPills';
import styles from './DashboardPage.module.scss';

type TimeOfDay = 'morning' | 'afternoon' | 'evening' | 'night';

function getTimeOfDay(): TimeOfDay {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return 'morning';
  if (hour >= 12 && hour < 17) return 'afternoon';
  if (hour >= 17 && hour < 21) return 'evening';
  return 'night';
}

export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const connectionStatus = useAuthStore((state) => state.connectionStatus);
  const serverVersion = useAuthStore((state) => state.serverVersion);
  const serverBuildDate = useAuthStore((state) => state.serverBuildDate);
  const config = useAuthStore((state) => state.config);

  const dashboardData = useDashboardData();

  const [timeOfDay, setTimeOfDay] = useState<TimeOfDay>(getTimeOfDay);
  const [currentTime, setCurrentTime] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => {
      setTimeOfDay(getTimeOfDay());
      setCurrentTime(new Date());
    }, 60_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className={styles.dashboard}>
      <div className={styles.backgroundOrbs} aria-hidden="true">
        <div className={styles.orb1} />
        <div className={styles.orb2} />
      </div>

      <HeroSection
        t={t}
        i18n={i18n}
        timeOfDay={timeOfDay}
        currentTime={currentTime}
        connectionStatus={connectionStatus}
        serverVersion={serverVersion}
        serverBuildDate={serverBuildDate}
        styles={styles}
      />

      <QuickStatsGrid
        t={t}
        dashboardData={dashboardData}
        styles={styles}
      />

      {config && (
        <ConfigPills
          t={t}
          config={config}
          styles={styles}
        />
      )}
    </div>
  );
}
```

注意：实际实施时需要仔细将 JSX 部分分配到子组件，保持所有样式引用正确。以上是骨架示意。

**Step 7: 运行测试确认通过**

```bash
cd temp_repo && npm test && npm run build
```

**Step 8: Commit**

```bash
git add temp_repo/src/hooks/useDashboardData.ts temp_repo/src/pages/DashboardPage/
git add temp_repo/src/pages/DashboardPage.tsx
git commit -m "refactor: split DashboardPage into sub-components with custom useDashboardData hook"
```

---

## Task 5: P2 — 硬编码字符串迁移 i18n

**Files:**
- Modify: `temp_repo/src/components/layout/MainLayout.tsx` (line 234)
- Modify: `temp_repo/src/i18n/locales/*.json` (添加 brand name i18n key)

**Step 1: 将 MainLayout 中硬编码品牌名移入 i18n**

当前代码 (line 234):
```typescript
const fullBrandName = 'CLI Proxy API Management Center';
```

修改为:
```typescript
const fullBrandName = t('brand.full_name');
```

**Step 2: 在 i18n locale 文件中添加翻译 key**

在各语言的翻译文件中添加:

```json
"brand": {
  "full_name": "CLI Proxy API Management Center",
  "abbr": "CPAMC"
}
```

注意：英文全名不变，中文可能需要翻译或保留英文。需要先查看现有 i18n 文件结构。

**Step 3: 运行测试和构建确认**

```bash
cd temp_repo && npm test && npm run build
```

**Step 4: Commit**

```bash
git add temp_repo/src/components/layout/MainLayout.tsx temp_repo/src/i18n/
git commit -m "refactor: move hardcoded brand name to i18n configuration"
```

---

## Task 6: P3 — Zustand selector 优化

**Files:**
- Modify: `temp_repo/src/pages/DashboardPage.tsx` (合并多个独立 selector)
- Modify: `temp_repo/src/components/layout/MainLayout.tsx` (合并 selector)
- Modify: `temp_repo/src/router/ProtectedRoute.tsx` (合并 selector)

**Step 1: 在 ProtectedRoute 中合并 selector**

当前代码 (line 8-11):
```typescript
const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
const managementKey = useAuthStore((state) => state.managementKey);
const apiBase = useAuthStore((state) => state.apiBase);
const checkAuth = useAuthStore((state) => state.checkAuth);
```

修改为使用单个 selector:

```typescript
const { isAuthenticated, managementKey, apiBase, checkAuth } = useAuthStore(
  (state) => ({
    isAuthenticated: state.isAuthenticated,
    managementKey: state.managementKey,
    apiBase: state.apiBase,
    checkAuth: state.checkAuth,
  })
);
```

注意：需要导入 `useShallow` from `zustand/react/shallow` 来避免引用比较导致的重渲染:

```typescript
import { useShallow } from 'zustand/react/shallow';

const { isAuthenticated, managementKey, apiBase, checkAuth } = useAuthStore(
  useShallow((state) => ({
    isAuthenticated: state.isAuthenticated,
    managementKey: state.managementKey,
    apiBase: state.apiBase,
    checkAuth: state.checkAuth,
  }))
);
```

**Step 2: 在 MainLayout 中合并相关 selector**

将 MainLayout 中的多个独立 auth/config/theme/language selector 合并为使用 `useShallow` 的组合 selector。

**Step 3: 在 DashboardPage 中合并 selector**

类似合并 DashboardPage 中的 useAuthStore/useConfigStore/useModelsStore selector。

**Step 4: 运行测试确认通过**

```bash
cd temp_repo && npm test && npm run build
```

**Step 5: Commit**

```bash
git add temp_repo/src/router/ProtectedRoute.tsx temp_repo/src/components/layout/MainLayout.tsx temp_repo/src/pages/DashboardPage.tsx
git commit -m "perf: optimize Zustand selectors using useShallow to reduce unnecessary re-renders"
```

---

## Task 7: P3 — 架构文档补充

**Files:**
- Create: `temp_repo/docs/ARCHITECTURE.md`

**Step 1: 创建架构文档**

创建 `temp_repo/docs/ARCHITECTURE.md`，内容包含:

1. 项目概览（技术栈、用途）
2. 目录结构图
3. 数据流图（API → Store → Component → UI）
4. 路由结构说明
5. 状态管理说明（Zustand stores 关系）
6. API Client 架构
7. 主题/语言系统说明
8. 构建和发布流程

**Step 2: Commit**

```bash
git add temp_repo/docs/ARCHITECTURE.md
git commit -m "docs: add architecture documentation covering data flow, routing, state management, and build process"
```

---

## Task 8: P1 — 项目结构重组 (temp_repo → frontend)

**Files:**
- Rename: `temp_repo/` → `frontend/`
- Modify: 根目录 README 更新引用路径
- Modify: `start_web.sh` / `start_web.bat` 更新前端路径引用（如有）

**Step 1: 重命名目录**

```bash
cd D:\UI\jztz_v17
mv temp_repo frontend
```

**Step 2: 更新根目录 README 中对 temp_repo 的引用**

将 README.md 中所有 `temp_repo` 引用替换为 `frontend`。

**Step 3: 检查并更新启动脚本**

检查 `start_web.sh` 和 `start_web.bat` 是否有对 temp_repo 的路径引用，如有则更新。

**Step 4: 验证前端构建仍然正常**

```bash
cd frontend && npm install && npm run build
```

**Step 5: Commit**

```bash
git add -A
git commit -m "refactor: rename temp_repo to frontend for clearer project structure"
```

---

## 执行顺序

建议按以下顺序执行（依赖关系考虑）：

1. **Task 2** (ProtectedRoute 修复) — 最小改动，独立，立即修复 bug
2. **Task 1** (Vitest 测试框架) — 基础设施，后续任务都依赖测试验证
3. **Task 3** (.env 体系) — 独立改进
4. **Task 8** (目录重命名) — 结构性改动，先做避免后续路径混乱
5. **Task 4** (DashboardPage 拆分) — 依赖 Task 1 的测试验证
6. **Task 5** (i18n 硬编码) — 小改动
7. **Task 6** (Zustand selector) — 依赖 Task 4 完成后的组件结构
8. **Task 7** (架构文档) — 最后补充，反映所有改动后的最终状态