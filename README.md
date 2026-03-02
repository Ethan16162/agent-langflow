# 📋项目概述
🎯 核心价值：让非技术人员也能通过对话创建复杂的 AI 应用流程

传统 Langflow 需要用户手动拖拽节点、配置参数、连接边，本项目在保留原有可视化编辑能力的基础上，引入智能 Copilot Agent，显著降低工作流构建门槛，提升开发效率。

Agent-Langflow 是一个创新的低代码增强方案，通过集成大语言模型（LLM）Agent，实现 「自然语言描述 → 可执行工作流」 的端到端自动生成。

# ✨ 核心功能
🔹 **智能工作流生成（Copilot Agent）**
- 自然语言理解：用户用日常语言描述需求（如"创建一个能回答 AI 问题的聊天机器人"），Agent 自动解析意图并生成符合 Langflow 规范的工作流
- 多轮对话迭代：支持上下文感知的多轮交互，用户可基于生成结果进一步调整优化
- 自动校验与修复：内置工作流结构校验、节点补全、边规范化机制，确保生成结果可直接运行

🔹 **自定义组件的自动注入**
- 支持用户自coding组件，并自动提取组件功能注入Agent，可以基于该组件构建workflow

# 🎬 效果演示
下面展示两个示例视频，演示运行效果：

- 基于Agent构建简单的问答系统：
<video src="files/Simple Agent.mp4" controls width="600">
你的浏览器不支持 video 标签，请下载查看：<a href="files/Simple Agent.mp4">Simple Agent</a>
</video>

- 基于Agent构建RAG系统：
<video src="files/RAG.mp4" controls width="600">
你的浏览器不支持 video 标签，请下载查看：<a href="files/RAG.mp4">RAG 示例</a>
</video>

## 🙏 Acknowledgements

本项目基于 [Langflow](https://github.com/langflow-ai/langflow) 框架开发，部署方案请参考 [原仓库文档](https://github.com/langflow-ai/langflow)。

