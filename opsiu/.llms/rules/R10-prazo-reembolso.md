---
id: R10
version: "2"
severidade: revisar
fundamento: "A documentação deve ser apresentada até 90 dias após o fornecimento do produto ou serviço."
---

# R10 — Prazo de apresentação da documentação

## Fundamento e limitação dos dados

O [Ato da Mesa 43, art. 4º, § 12](https://www2.camara.leg.br/legin/int/atomes/2009/atodamesa-43-21-maio-2009-588364-normaatualizada-cd-mesa.html)
fixa o prazo após o fornecimento do produto ou serviço. O
[dicionário oficial CEAP](https://dadosabertos.camara.leg.br/howtouse/2023-12-26-dados-ceap.html)
distingue a emissão do documento da competência financeira. Nenhuma delas
comprova a data de apresentação ou, necessariamente, a do fornecimento.

A versão 1 usava o primeiro dia da competência como apresentação e a emissão
como fornecimento. Um intervalo superior a 90 dias entre esses campos não
sustenta essa inferência. A versão 2 suspende a geração de achados.

## Comportamento determinístico

A suspensão é explícita e versionada. `check --regras R10` registra o motivo
no evento `rule.suspended` e marca a etapa como ignorada, sem avaliar despesas.
Em execução normal, substitui os achados anteriores por eventos imutáveis de
supersessão. Preserva documentos, decisões, pareceres e rastros de agentes.
Não registra uma decisão humana de rejeição nem declara a despesa regular.

Em `--dry-run`, não altera achados nem decisões. Uma fonte temporariamente
ausente em outra regra continua apenas ignorada, sem retirar achados anteriores;
a suspensão por fundamento inválido é uma situação distinta.

## Condições para reativação

Uma nova versão deve exigir datas comprovadas de fornecimento e apresentação,
com identidade documental e proveniência das fontes. A ausência dessas datas
não equivale a zero dias de atraso. A implementação precisa testar prazo exato,
datas ausentes e divergentes, e situações em que emissão e fornecimento diferem.

## Orientação para revisão e agentes

Pareceres antigos continuam históricos. Não transformar competência em data
de protocolo nem concluir atraso a partir da versão suspensa. O motivo da
retirada é insuficiência do fundamento, não comprovação de regularidade.
