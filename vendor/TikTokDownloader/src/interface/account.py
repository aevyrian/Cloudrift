from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Callable, Coroutine, Type, Union

from src.interface.template import API
from src.translation import _

if TYPE_CHECKING:
    from src.config import Parameter
    from src.testers import Params


class Account(API):
    post_api = f"{API.domain}aweme/v1/web/aweme/post/"
    favorite_api = f"{API.domain}aweme/v1/web/aweme/favorite/"

    def __init__(
        self,
        params: Union["Parameter", "Params"],
        cookie: str = "",
        proxy: str | None = None,
        sec_user_id: str = ...,
        tab="post",
        earliest: str | float | int = "",
        latest: str | float | int = "",
        pages: int = None,
        cursor=0,
        count=18,
        *args,
        **kwargs,
    ):
        super().__init__(params, cookie, proxy, *args, **kwargs)
        self.sec_user_id = sec_user_id
        self.api, self.favorite, self.pages = self.check_type(
            tab, pages or params.max_pages
        )
        # TODO: 重构数据验证逻辑
        self.latest: date = self.check_latest(latest)
        self.earliest: date = self.check_earliest(earliest)
        self.cursor = cursor
        self.count = count
        self.text = _("账号喜欢作品") if self.favorite else _("账号发布作品")

    async def run(
        self,
        referer: str = None,
        single_page=False,
        data_key: str = "aweme_list",
        error_text="",
        cursor="max_cursor",
        has_more="has_more",
        params: Callable = lambda: {},
        data: Callable = lambda: {},
        method="GET",
        headers: dict = None,
        *args,
        **kwargs,
    ):
        if self.favorite:
            self.set_referer(f"{self.domain}user/{self.sec_user_id}?showTab=like")
        else:
            self.set_referer(f"{self.domain}user/{self.sec_user_id}")
        match single_page:
            case True:
                await self.run_single(
                    data_key,
                    error_text
                    or _(
                        "该账号为私密账号，需要使用登录后的 Cookie，且登录的账号需要关注该私密账号"
                    ),
                    cursor,
                    has_more,
                    params,
                    data,
                    method,
                    headers,
                    *args,
                    **kwargs,
                )
                return self.response
            case False:
                await self.run_batch(
                    data_key,
                    error_text
                    or _(
                        "该账号为私密账号，需要使用登录后的 Cookie，且登录的账号需要关注该私密账号"
                    ),
                    cursor,
                    has_more,
                    params,
                    data,
                    method,
                    headers,
                    *args,
                    **kwargs,
                )
                return self.response, self.earliest, self.latest
        raise ValueError

    async def run_single(
        self,
        data_key: str = "aweme_list",
        error_text="",
        cursor="max_cursor",
        has_more="has_more",
        params: Callable = lambda: {},
        data: Callable = lambda: {},
        method="GET",
        headers: dict = None,
        *args,
        **kwargs,
    ):
        await super().run_single(
            data_key,
            error_text,
            cursor,
            has_more,
            params,
            data,
            method,
            headers,
            *args,
            **kwargs,
        )

    async def run_batch(
        self,
        data_key: str = "aweme_list",
        error_text="",
        cursor="max_cursor",
        has_more="has_more",
        params: Callable = lambda: {},
        data: Callable = lambda: {},
        method="GET",
        headers: dict = None,
        callback: Type[Coroutine] = None,
        *args,
        **kwargs,
    ):
        seen_cursors = {str(self.cursor)}
        seen_ids = set()
        previous_count, stale_pages = len(self.response), 0

        async def check_progress():
            nonlocal previous_count, stale_pages
            ids = {str(item.get("aweme_id") or item.get("id"))
                   for item in self.response[previous_count:] if isinstance(item, dict)
                   and (item.get("aweme_id") or item.get("id"))}
            previous_count = len(self.response)
            stale_pages = 0 if ids - seen_ids else stale_pages + 1
            seen_ids.update(ids)
            if not self.finished:
                if str(self.cursor) in seen_cursors:
                    raise RuntimeError("博主分页游标重复，已中止，可重试")
                if stale_pages >= 3:
                    raise RuntimeError("连续三页没有新作品，已中止，可重试")
                seen_cursors.add(str(self.cursor))
            if callback:
                await callback()
            else:
                await self.early_stop()

        await super().run_batch(
            data_key,
            error_text,
            cursor,
            has_more,
            params,
            data,
            method,
            headers,
            callback=check_progress,
            *args,
            **kwargs,
        )
        self.summary_works()

    async def early_stop(self):
        """如果获取数据的发布日期已经早于限制日期，就不需要再获取下一页的数据了"""
        if (
            not self.favorite
            and self.earliest
            > datetime.fromtimestamp(max(int(self.cursor) / 1000, 0)).date()
        ):
            self.finished = True

    def generate_params(
        self,
    ) -> dict:
        match self.favorite:
            case True:
                return self.generate_favorite_params()
            case False:
                return self.generate_post_params()
        return {}

    def generate_favorite_params(self) -> dict:
        return self.params | {
            "sec_user_id": self.sec_user_id,
            "max_cursor": self.cursor,
            "min_cursor": "0",
            "whale_cut_token": "",
            "cut_version": "1",
            "count": self.count,
            "publish_video_strategy_type": "2",
            "version_code": "170400",
            "version_name": "17.4.0",
        }

    def generate_post_params(self) -> dict:
        return self.params | {
            "sec_user_id": self.sec_user_id,
            "max_cursor": self.cursor,
            "locate_query": "false",
            "show_live_replay_strategy": "1",
            "need_time_list": "1",
            "time_list_query": "0",
            "whale_cut_token": "",
            "cut_version": "1",
            "count": self.count,
            "publish_video_strategy_type": "2",
            "from_user_page": "1",
        }

    def check_type(self, tab: str, pages: int) -> tuple[str, bool, int]:
        match tab:
            case "favorite":
                return self.favorite_api, True, pages
            case "post":
                pass
            case _:
                self.log.warning(
                    _("tab 参数 {tab} 设置错误，程序将使用默认值: post").format(tab=tab)
                )
        return self.post_api, False, 99999

    def check_earliest(self, date_: str | float | int) -> date:
        return self.check_date(date(2016, 9, 20), self.latest, _("最早"), date_)

    def check_latest(self, date_: str | float | int) -> date:
        return self.check_date(date.today(), date.today(), _("最晚"), date_)

    def check_date(
        self, default: date, start: date, tip: str, value: str | float | int
    ) -> date:
        if not value:
            return default
        if isinstance(value, (int, float)):
            date_ = start - timedelta(days=value)
        elif isinstance(value, str):
            try:
                date_ = datetime.strptime(value, "%Y/%m/%d").date()
            except ValueError:
                self.log.warning(
                    _("作品{tip}发布日期无效 {date}").format(tip=tip, date=value)
                )
                return default
        else:
            raise ValueError(
                _("作品{tip}发布日期参数 {date} 类型错误").format(tip=tip, date=value)
            )
        self.log.info(
            _("作品{tip}发布日期: {latest_date}").format(tip=tip, latest_date=date_)
        )
        return date_  # 返回 date 对象

    def check_response(
        self,
        data_dict: dict,
        data_key: str,
        error_text="",
        cursor="cursor",
        has_more="has_more",
        *args,
        **kwargs,
    ):
        if (not isinstance(data_dict, dict)
                or data_dict.get("status_code", 0) != 0
                or not isinstance(data_dict.get(data_key), list)
                or data_dict.get(has_more) not in (0, 1, False, True)):
            raise RuntimeError("博主分页响应无效或访问受限，未获取完整作品，可重试")
        more = bool(data_dict[has_more])
        if more and data_dict.get(cursor) is None:
            raise RuntimeError("博主分页缺少下一页游标，未获取完整作品，可重试")
        self.cursor = data_dict.get(cursor, self.cursor)
        self.append_response(data_dict[data_key])
        self.finished = not more


async def test():
    from src.testers import Params

    async with Params() as params:
        i = Account(
            params,
            sec_user_id="",
        )
        print(await i.run())


if __name__ == "__main__":
    from asyncio import run

    run(test())
