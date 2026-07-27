export const BUILTIN_AGENT_ID = 'default-chatbot'
export const RESEARCH_COPILOT_AGENT_ID = 'research-copilot'

const hasAgentId = (agent, agentId) => agent?.id === agentId || agent?.slug === agentId

export const isDefaultBuiltinAgent = (agent) => hasAgentId(agent, BUILTIN_AGENT_ID)
export const isResearchCopilotAgent = (agent) => hasAgentId(agent, RESEARCH_COPILOT_AGENT_ID)

export const isBuiltinAgent = (agent) =>
  Boolean(
    agent?.is_builtin
      || isDefaultBuiltinAgent(agent)
      || isResearchCopilotAgent(agent)
  )

export const getPreferredAgentId = (agents, persistedId) => {
  const chatAgents = agents.filter((agent) => !agent.is_subagent)
  if (persistedId && chatAgents.some((agent) => agent.id === persistedId)) {
    return persistedId
  }
  // 默认使用科研助手，复用框架原生对话页的工具调用展示能力
  return chatAgents.find(isResearchCopilotAgent)?.id
    || chatAgents.find(isDefaultBuiltinAgent)?.id
    || chatAgents.find((agent) => !isBuiltinAgent(agent))?.id
    || null
}
