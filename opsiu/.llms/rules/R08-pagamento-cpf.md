---
id: R08
version: "1"
severidade: alta
fundamento: "pagamento realizado à pessoa física, salvo para locação de imóvel, uso de aeronave ou embarcação, e serviços de táxi;"
---

# R08 — Pagamento a CPF fora das categorias de exceção

## Fundamento e alcance

A [fonte](../../cota-parlamentar.md), na seção de despesas não admitidas,
restringe pagamentos a pessoas físicas e enumera exceções. Esta regra compara
o tipo de favorecido com a categoria registrada na despesa corrente.

A classificação `CPF` resulta do identificador normalizado com onze dígitos;
ela não comprova validade cadastral. As exceções disponíveis são por categoria:
`1` para manutenção de escritório, que inclui locação de imóvel; `119` para
aeronaves; `121` para embarcações; e `122` para táxi, pedágio e estacionamento.

A categoria `1` não distingue aluguel dos demais gastos de escritório, e a
categoria `122` não distingue táxi de pedágio e estacionamento. A dispensa da
checagem nessas categorias é uma limitação da granularidade do dado, não uma
conclusão de que todos os seus serviços podem ser pagos a pessoa física.

## Campos e lógica determinística

Usar `despesas_current`, com `payer_kind`, `cnpj_cpf_digits`,
`numero_sub_cota`, `descricao`, `valor_liquido` e `id_documento`, mantendo a
identificação do parlamentar e da competência como contexto.

Emitir um achado por documento quando `payer_kind = CPF` e a categoria estiver
fora de `{1, 119, 121, 122}`. Favorecidos classificados como CNPJ ou OUTRO não
disparam esta regra. CPF em categoria de exceção também não dispara. Não
presumir detalhes do serviço que não estejam representados na categoria.

O valor do achado é `valor_liquido` do documento, em centavos exatos. A
proveniência mantém o vínculo com arquivo, registro, execução, etapa e versão.

## Evidência

Os campos são `payer_kind`, `cnpj_cpf_digits`, `numero_sub_cota`, `descricao`
e `valor_liquido`. O valor monetário é serializado como texto decimal exato.
A evidência permite conferir a aplicação da exceção sem atribuir à categoria
uma precisão que a fonte não possui. O documento associado fornece o contexto
do parlamentar e da competência.

## Exemplos sintéticos

No arquivo `Ano-2099.json`, o parlamentar `201` registra cinco pagamentos de
R$ 25,00 ao CPF sintético `000.000.000-01` em maio:

- `209900040`, categoria `3`: gera achado de R$ 25,00.
- `209900041`, categoria `1`: não dispara a checagem por categoria.
- `209900042`, categoria `119`: não dispara.
- `209900043`, categoria `121`: não dispara.
- `209900044`, categoria `122`: não dispara a checagem por categoria.

Os identificadores são fictícios e servem apenas para testar normalização e
aplicação das condições.

## O que o agente deve verificar

Confira o favorecido, a descrição do serviço, a categoria e o valor líquido
nos documentos disponíveis. Aponte divergências de identificação ou
classificação e descreva os limites das categorias de exceção. Não invente
vínculo cadastral nem deduza o serviço apenas pelo número do CPF.

O agente anota o achado produzido pelo código; não decide o disparo da regra.
A aprovação para o relatório final depende da revisão humana.
