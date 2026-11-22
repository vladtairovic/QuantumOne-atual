"""Regressões de falhas transitórias do servidor de documentação."""
import unittest
from unittest.mock import patch

import httpx

from rag.coleta_web import baixar


class ColetaRetryTests(unittest.TestCase):
    def executar(self, respostas):
        self.urls = []
        self.pausas = []

        def responder(req):
            self.urls.append(str(req.url))
            resposta = next(respostas)
            if isinstance(resposta, Exception):
                raise resposta
            return resposta

        with httpx.Client(transport=httpx.MockTransport(responder)) as cliente:
            with patch("rag.coleta_web.time.sleep", side_effect=self.pausas.append):
                return baixar(cliente, "https://quantum.cloud.ibm.com/docs/en/a", com_url=True)

    def test_recupera_503_e_preserva_url_final(self):
        resultado = self.executar(iter([
            httpx.Response(302, headers={"location": "/docs/en/b"}),
            httpx.Response(503),
            httpx.Response(200, headers={"content-type": "text/html"}, text="documentação"),
        ]))
        self.assertEqual(resultado, ("documentação", "https://quantum.cloud.ibm.com/docs/en/b"))
        self.assertTrue(self.pausas)

    def test_falha_persistente_tem_limite(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self.executar(iter([httpx.Response(503) for _ in range(3)]))
        self.assertEqual(len(self.urls), 3)

    def test_nao_repete_404(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self.executar(iter([httpx.Response(404)]))
        self.assertEqual(len(self.urls), 1)
        self.assertEqual(self.pausas, [])

    def test_recupera_timeout(self):
        self.assertEqual(self.executar(iter([
            httpx.ReadTimeout("timeout"),
            httpx.Response(200, headers={"content-type": "text/plain"}, text="ok"),
        ]))[0], "ok")

    def test_redirect_inseguro_apos_retry_continua_bloqueado(self):
        with self.assertRaises(ValueError):
            self.executar(iter([
                httpx.Response(503),
                httpx.Response(302, headers={"location": "http://127.0.0.1/segredo"}),
            ]))
        self.assertEqual(len(self.urls), 2)
