#!/usr/bin/env python3
import asyncio
import argparse
from datetime import datetime, timedelta
import pathlib
import random
import string
import sys
import subprocess
import os
import time
import threading
from subprocess import Popen
from decimal import Decimal
from yapapi.props import inf, com

import colorama

from yapapi import Golem
from yapapi.contrib.service.socket_proxy import SocketProxy, SocketProxyService
from yapapi.payload import vm

from yapapi.events import (
    ProposalRejected,
    AgreementConfirmed,
    TaskAccepted,
    DebitNoteAccepted,
    InvoiceAccepted,
    ShutdownFinished
)

from yapapi import events

from yapapi.contrib.strategy import ProviderFilter

from yapapi.strategy import (
    LeastExpensiveLinearPayuMS,
    PROP_DEBIT_NOTE_INTERVAL_SEC,
    PROP_PAYMENT_TIMEOUT_SEC,
    PropValueRange
)

from yapapi.log import enable_default_logger

TEXT_COLOR_RED = "\033[31;1m"
TEXT_COLOR_GREEN = "\033[32;1m"
TEXT_COLOR_YELLOW = "\033[33;1m"
TEXT_COLOR_BLUE = "\033[34;1m"
TEXT_COLOR_MAGENTA = "\033[35;1m"
TEXT_COLOR_CYAN = "\033[36;1m"
TEXT_COLOR_WHITE = "\033[37;1m"
TEXT_COLOR_DEFAULT = "\033[0m"

colorama.init()

bad_providers = set()

#class ShortDebitNoteIntervalAndPaymentTimeout(LeastExpensiveLinearPayuMS):
#    def __init__(self, expected_time_secs, max_fixed_price, max_price_for, interval_payment):
#        super().__init__(expected_time_secs, max_fixed_price, max_price_for)
        #if interval_payment != 0:
        #    self.interval_payment = interval_payment
        #    self.acceptable_prop_value_range_overrides = {
        #        PROP_DEBIT_NOTE_INTERVAL_SEC: PropValueRange(self.interval_payment, math.floor((self.interval_payment*6)/5)),
        #        PROP_PAYMENT_TIMEOUT_SEC: PropValueRange(60, 70),
        #    }

class SshService(SocketProxyService):
    remote_port = 22

    def __init__(self, proxy: SocketProxy):
        super().__init__()
        self.proxy = proxy

    @staticmethod
    async def get_payload():
        return await vm.repo(
            image_hash="fb77d28153df7e9258e955c7fc8b2ba17f3ebb96b17e1190e3c5bced",
            min_mem_gib=1,
            min_storage_gib=1,
            min_cpu_threads=4,
            capabilities=[vm.VM_CAPS_VPN],
        )

    async def start(self):
        async for script in super().start():
            yield script

        password = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))

        script = self._ctx.new_script(timeout=timedelta(minutes=10))

        script.run("/bin/bash", "-c", "ssh-keygen -A")
        script.run("/bin/bash", "-c", f'echo -e "{password}\n{password}" | passwd')
        script.run("/bin/bash", "-c", "/usr/sbin/sshd &")

        yield script

        server_ssh = await self.proxy.run_server(self, self.remote_port)

        print(
            f"{TEXT_COLOR_CYAN}connect with:\n"
            f"ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {server_ssh.local_port} root@{server_ssh.local_address}{TEXT_COLOR_DEFAULT}"
        )
        print(f"{TEXT_COLOR_RED}password: {password}{TEXT_COLOR_DEFAULT}")

        script = self._ctx.new_script(timeout=timedelta(hours=24))
        script.run("/bin/bash", "-c", "xmrig -c /usr/share/config.json")

        Popen(["/home/norbert/yapapi/examples/ssh/start.sh", f"{password}", f"{server_ssh.local_port}", "&"])

        yield script


async def main(subnet_tag, payment_driver=None, payment_network=None, num_instances=1):

    def event_consumer(event: events.Event):
        if isinstance(event, events.ActivityCreateFailed):
            bad_providers.add(event.provider_id)
        elif isinstance(event, events.TaskRejected):
            bad_providers.add(event.provider_id)
        elif isinstance(event, events.WorkerFinished):
            bad_providers.add(event.provider_id)

    golem = Golem(
        budget=10,
        subnet_tag=subnet_tag,
        payment_driver=payment_driver,
        payment_network=payment_network,
    )

    golem.strategy = ProviderFilter(golem.strategy, lambda provider_id: provider_id not in bad_providers)

    #golem.strategy = ProviderFilter(ShortDebitNoteIntervalAndPaymentTimeout(
    #    expected_time_secs=3600,
    #    max_fixed_price=,
    #    max_price_for={
    #        com.Counter.CPU: 0.1,
    #        com.Counter.TIME: 0.1
    #    },
    #    interval_payment=0
    #), lambda provider_id: provider_id not in bad_providers)

    async with golem:

        golem.add_event_consumer(event_consumer)

        network = await golem.create_network("192.168.0.1/16")
        proxy = SocketProxy(ports=range(2222, 2222 + 10*num_instances))

        async with network:
            cluster = await golem.run_service(
                SshService,
                network=network,
                num_instances=num_instances,
                instance_params=[{"proxy": proxy} for _ in range(num_instances)]
            )
            instances = cluster.instances

            while True:
                #print(instances)
                try:
                    await asyncio.sleep(5)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    break

            await proxy.stop()
            cluster.stop()

            cnt = 0
            while cnt < 3 and any(s.is_available for s in instances):
                #print(instances)
                await asyncio.sleep(5)
                cnt += 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--num-instances",
        type=int,
        default=10,
        help="Number of instances to spawn",
    )
    now = datetime.now().strftime("%Y-%m-%d_%H.%M.%S")
    parser.set_defaults(log_file=f"ssh-yapapi-{now}.log")
    args = parser.parse_args()

    enable_default_logger(
        debug_activity_api=True,
        debug_market_api=True,
        debug_payment_api=True,
        debug_net_api=True,
    )

    loop = asyncio.get_event_loop()
    task = loop.create_task(main(
        subnet_tag="public",
        #args.subnet_tag,
        payment_driver="erc20",
        #"polygon",
        #args.payment_driver,
        payment_network="polygon",
        #args.payment_network,
        num_instances=args.num_instances))

    loop.run_until_complete(task)